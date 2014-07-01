import requests
import json
import oauth2 as oauth
import http.client
from datetime import timedelta, datetime
import time
from requests_oauthlib import OAuth1
from nio.common.block.base import Block
from nio.common.discovery import Discoverable, DiscoverableType
from nio.metadata.properties.holder import PropertyHolder
from nio.metadata.properties.list import ListProperty
from nio.metadata.properties.timedelta import TimeDeltaProperty
from nio.metadata.properties.object import ObjectProperty
from nio.metadata.properties.string import StringProperty
from nio.modules.scheduler.imports import Job
from nio.common.signal.base import Signal
from nio.modules.threading.imports import Lock, spawn, Event


VERIFY_CREDS_URL = ('https://api.twitter.com/1.1/'
                    'account/verify_credentials.json')
STREAMING_URL = 'https://stream.twitter.com/1.1/statuses/filter.json'
ONE_MIN = timedelta(seconds=60)
FIVE_SEC = timedelta(seconds=5)
TCPERR_INT = timedelta(milliseconds=250)


class TwitterCreds(PropertyHolder):

    """ Property holder for Twitter OAuth credentials.

    """
    consumer_key = StringProperty()
    app_secret = StringProperty()
    oauth_token = StringProperty()
    oauth_token_secret = StringProperty()


class Tweet(Signal):

    """ A Twitter signal.

    Args:
        tweet (dict): A single tweet, JSON deserialized.

    """

    def __init__(self, tweet):
        for field in tweet:
            setattr(self, field, tweet[field])


@Discoverable(DiscoverableType.block)
class Twitter(Block):

    """ A block for communicating with the Twitter Streaming API.
    Reads Tweets in real time, notifying other blocks via NIO's signal
    interface at a configurable interval.

    Properties:
        phrases (list(str)): The list of phrases to track.
        fields (list(str)): Outgoing signals will pull these fields
            from incoming tweets. When empty/unset, all fields are
            included.
        notify_freq (timedelta): The interval between signal notifications.
        creds: Twitter app credentials, see above. Defaults to global settings.
        rc_interval (timedelta): Time to wait between receipts (either tweets
            or hearbeats) before attempting to reconnect to Twitter Streaming.

    """
    phrases = ListProperty(str)
    fields = ListProperty(str)
    notify_freq = TimeDeltaProperty(default={"seconds": 2})
    creds = ObjectProperty(TwitterCreds)
    rc_interval = TimeDeltaProperty(default={"seconds": 90})

    def __init__(self):
        super().__init__()
        self._tweets = []
        self._tweet_lock = Lock()
        self._stop_event = Event()
        self._stream = None
        self._last_rcv = datetime.utcnow()

        # Jobs to run throughout execution
        self._notify_job = None    # notifies signals
        self._monitor_job = None   # checks for heartbeats
        self._rc_job = None        # attempts reconnects
        self._rc_delay = timedelta(seconds=1)

    def start(self):
        super().start()
        self._authorize()
        spawn(self._stream_tweets)
        self._notify_job = Job(
            self._notify_tweets,
            self.notify_freq,
            True
        )

    def stop(self):
        self._stop_event.set()
        self._notify_job.cancel()
        if self._monitor_job is not None:
            self._monitor_job.cancel()
        if self._rc_job is not None:
            self._rc_job.cancel()
        super().stop()

    def _stream_tweets(self):
        """ The main thread for the Twitter block. Reads from Twitter
        streaming, parses and queues results.

        """

        # If we had an existing stream, close it. We will open our own
        if self._stream:
            self._stream.close()
            self._stream = None

        # Try to connect, if we can't, don't start streaming, but try reconnect
        if not self._connect_to_streaming():
            self._setup_reconnect_attempt()
            return

        while(1):
            if self._stop_event.is_set():
                break

            tweet = None
            try:
                tweet = self._read_tweet()
            except Exception as e:
                # Error while getting the tweet, this probably indicates a
                # disconnection so let's try to reconnect
                self._logger.error("While streaming tweets: %s" % str(e))
                self._setup_reconnect_attempt()
                break

            if tweet and len(tweet):
                self._record_tweet(tweet)

    def _read_tweet(self):
        """Read the next tweet off of the stream.

        This will first read the length of the tweet, then read the next
        N bytes based on the length. It will return the read tweet if it reads
        successfully. Otherwise, returns None.

        Raises:
            Exception: if there was an error reading bytes - this will most
                likely indicate a disconnection
        """
        # build the length buffer
        buf = bytes('', 'utf-8')
        while not buf or buf[-1] != ord('\n'):
            bytes_read = self._read_bytes(1)
            if bytes_read:
                buf += bytes_read
            else:
                raise Exception("No bytes read from stream")

        # checking to see if it's a 'keep-alive'
        if len(buf) <= 2:
            # only recieved \r\n so it is a keep-alive. move on.
            self._logger.debug('Received a keep-alive signal from Twitter.')
            self._last_rcv = datetime.utcnow()
            return None

        return self._read_bytes(int(buf))

    def _read_bytes(self, n_bytes):
        """Read N bytes off of the current stream.

        Returns:
            len (int): number of bytes actually read - None if no bytes read
        """
        bytes_read = self._stream.read(n_bytes)
        return bytes_read if len(bytes_read) > 0 else None

    def _connect_to_streaming(self):
        """Set up a connection to the Twitter Streaming API.

        This method will build the connection and save it in self._stream. On
        a valid connection, it will reset the reconnection and monitoring jobs

        Returns
            success (bool): Whether or not the connection succeeded. If any
                errors occur during connection, it will not schedule the
                reconnects, but rather just return False.
        """

        try:
            self._conn = http.client.HTTPSConnection(
                host='stream.twitter.com',
                timeout=45)

            req_params = {
                'stall_warnings': 'true',
                'delimited': 'length',
                'track': ','.join(self.phrases)
            }
            req_headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': '*/*'
            }

            # get the signed request with the proper oauth creds
            req = self._getOauthRequest(req_params)

            self._conn.request('POST',
                               STREAMING_URL,
                               body=req.to_postdata(),
                               headers=req_headers)
            response = self._conn.getresponse()

            if response.status != 200:
                self._logger.warning(
                    'Status:{0} returned from twitter'.format(response.status))
                return False
            else:
                self._logger.debug('Connected to Streaming API Successfully')

                # Clear any reconnects we had
                if self._rc_job is not None:
                    self._logger.debug("We were reconnecting, now we're done!")
                    self._rc_job.cancel()
                    self._rc_delay = timedelta(seconds=1)
                    self._rc_job = None

                self._last_rcv = datetime.utcnow()

                self._monitor_job = Job(
                    self._monitor_connection,
                    self.rc_interval,
                    True
                )

                self._stream = response
                # Return true, we are connected!
                return True

        except Exception as e:
            self._logger.error('Error opening connection : {0}'.format(e))
            return False

    def _setup_reconnect_attempt(self):
        """Add the reconnection job and double the delay for the next one"""
        if self._monitor_job is not None:
            self._monitor_job.cancel()

        self._logger.debug("Reconnecting in %d seconds" %
                           self._rc_delay.total_seconds())
        self._rc_job = Job(self._stream_tweets,
                           self._rc_delay, False)
        self._rc_delay *= 2

    def _getOauthRequest(self, request_params):
        """This function uses the oauthCreds passed from the transducer to
        sign the request.
        """
        request_params['oauth_version'] = '1.0'
        request_params['oauth_nonce'] = oauth.generate_nonce()
        request_params['oauth_timestamp'] = int(time.time())

        req = oauth.Request(method='POST',
                            url=STREAMING_URL,
                            parameters=request_params)

        req.sign_request(
            signature_method=oauth.SignatureMethod_HMAC_SHA1(),
            consumer=oauth.Consumer(
                self.creds.consumer_key, self.creds.app_secret),
            token=oauth.Token(
                self.creds.oauth_token, self.creds.oauth_token_secret)
        )

        return req

    def _record_tweet(self, tweet):
        """ Decode the tweet and add it to the end of the list

        """
        try:
            # reset the last received timestamp
            self._last_rcv = datetime.utcnow()

            data = self._select_fields(json.loads(tweet.decode('utf-8')))
            tw = Tweet(data)
            with self._tweet_lock:
                self._tweets.append(tw)
        except Exception as e:
            self._logger.error("Could not parse Tweet: %s" % str(e))

    def _select_fields(self, data):
        """ Filters incoming tweet objects to include only the configured
        fields (or all of them, if self.fields is empty).

        """
        # If they did not specify which fields, just give them everything
        if not self.fields or len(self.fields) == 0:
            return data

        result = {}
        for f in self.fields:
            try:
                result[f] = data[f]
            except:
                self._logger.error("Invalid Twitter field: %s" % f)

        return result

    def _notify_tweets(self):
        """Method to be called from the notify job, will notify any tweets
        that have been buffered by the block, then clear the buffer.
        """
        with self._tweet_lock:
            if len(self._tweets) == 0:
                return

            self.notify_signals(self._tweets)
            self._tweets = []

    def _monitor_connection(self):
        """ Scheduled to run every self.rc_interval. Makes sure that some
        data has been received in the last self.rc_interval.

        """
        current_time = datetime.utcnow()
        time_since_data = current_time - self._last_rcv
        if time_since_data > self.rc_interval:
            self._logger.warning("No data received, we might be disconnected")
            self._reconnect()

    def _authorize(self):
        """ Prepare the OAuth handshake and verify.

        """
        try:
            auth = OAuth1(self.creds.consumer_key,
                          self.creds.app_secret,
                          self.creds.oauth_token,
                          self.creds.oauth_token_secret)
            resp = requests.get(VERIFY_CREDS_URL, auth=auth)
            if resp.status_code != 200:
                raise Exception("Status %s" % resp.status_code)
        except Exception:
            self._logger.error("Authentication Failed"
                               "for consumer key: %s" %
                               self.creds.consumer_key)
