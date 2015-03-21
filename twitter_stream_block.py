import requests
import json
import oauth2 as oauth
import http.client
from datetime import timedelta, datetime
import time
from collections import defaultdict
from requests_oauthlib import OAuth1
from nio.common.block.base import Block
from nio.common.block.attribute import Output
from nio.metadata.properties import PropertyHolder, TimeDeltaProperty, \
    ObjectProperty, StringProperty
from nio.modules.scheduler import Job
from nio.common.signal.base import Signal
from nio.modules.threading import Lock, spawn, Event


PUB_STREAM_MSGS = {
    'limit': "Limit",
    'delete': "Deletion",
    'scrub_geo': "Location Deletion",
    'status_witheld': "Status Witheld",
    'user_witheld': "User Witheld",
    'disconnect': "Disconnect",
    'warning': "Stall Warning"
}


DISCONNECT_REASONS = [
    "Shutdown",
    "Duplicate stream",
    "Control request",
    "Stall",
    "Normal",
    "Token revoked",
    "Admin revoked",
    '',
    "Max message limit",
    "Stream exception",
    "Broker stall",
    "Shed load"
]


class TwitterCreds(PropertyHolder):

    """ Property holder for Twitter OAuth credentials.

    """
    consumer_key = StringProperty(title='API Key', default="[[TWITTER_API_KEY]]")
    app_secret = StringProperty(title='API Secret', default="[[TWITTER_API_SECRET]]")
    oauth_token = StringProperty(title='Access Token', default="[[TWITTER_ACCESS_TOKEN]]")
    oauth_token_secret = StringProperty(title='Access Token Secret', default="[[TWITTER_ACCESS_TOKEN_SECRET]]")


@Output("other")
@Output("limit")
class TwitterStreamBlock(Block):

    """ A parent block for communicating with the Twitter Streaming API.

    Properties:
        fields (list(str)): Outgoing signals will pull these fields
            from incoming tweets. When empty/unset, all fields are
            included.
        notify_freq (timedelta): The interval between signal notifications.
        creds: Twitter app credentials, see above. Defaults to global settings.
        rc_interval (timedelta): Time to wait between receipts (either tweets
            or hearbeats) before attempting to reconnect to Twitter Streaming.

    """
    notify_freq = TimeDeltaProperty(default={"seconds": 2},
                                    title='Notification Frequency')
    creds = ObjectProperty(TwitterCreds, title='Credentials',
                           default=TwitterCreds())
    rc_interval = TimeDeltaProperty(default={"seconds": 90},
                                    title='Reconnect Interval')

    streaming_host = None
    streaming_endpoint = None
    verify_url = 'https://api.twitter.com/1.1/account/verify_credentials.json'

    def __init__(self):
        super().__init__()
        self._result_signals = defaultdict(list)
        self._result_lock = defaultdict(Lock)
        self._lock_lock = Lock()
        self._stop_event = Event()
        self._stream = None
        self._last_rcv = datetime.utcnow()
        self._limit_count = 0
        self._limit_lock = Lock()

        # Jobs to run throughout execution
        self._notify_job = None    # notifies signals
        self._monitor_job = None   # checks for heartbeats
        self._rc_job = None        # attempts reconnects
        self._rc_delay = timedelta(seconds=1)

    def start(self):
        super().start()
        self._authorize()
        self._start()
        spawn(self._run_stream)
        self._notify_job = Job(
            self._notify_results,
            self.notify_freq,
            True
        )

    def _start(self):
        """ Override in blocks that need to run code before start """
        pass

    def stop(self):
        self._stop_event.set()
        self._notify_job.cancel()
        if self._monitor_job is not None:
            self._monitor_job.cancel()
        if self._rc_job is not None:
            self._rc_job.cancel()
        super().stop()

    def _run_stream(self):
        """ The main thread for the Twitter block. Reads from Twitter
        streaming, parses and queues results.

        """

        # If we had an existing stream, close it. We will open our own
        if self._stream:
            self._stream.close()
            self._stream = None

        # This is a new stream so reset the limit count
        self._limit_count = 0

        # Try to connect, if we can't, don't start streaming, but try reconnect
        if not self._connect_to_streaming():
            self._setup_reconnect_attempt()
            return

        while(1):
            if self._stop_event.is_set():
                break

            line = None
            try:
                line = self._read_line()
            except Exception as e:
                # Error while getting the tweet, this probably indicates a
                # disconnection so let's try to reconnect
                self._logger.error("While streaming: %s" % str(e))
                self._setup_reconnect_attempt()
                break

            if line and len(line):
                self._record_line(line)

    def _read_line(self):
        """Read the next line off of the stream.

        This will first read the length of the line, then read the next
        N bytes based on the length. It will return the read line if it reads
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

    def get_params(self):
        """ Return URL connection parameters here """
        return {}

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
                host=self.streaming_host,
                timeout=45)

            req_headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': '*/*'
            }

            conn_url = 'https://{0}/{1}'.format(
                self.streaming_host,
                self.streaming_endpoint)

            # get the signed request with the proper oauth creds
            req = self._get_oauth_request(conn_url, self.get_params())

            self._logger.debug("Connecting to {0}".format(conn_url))

            if self.get_request_method() == "POST":
                self._conn.request(self.get_request_method(),
                                   conn_url,
                                   body=req.to_postdata(),
                                   headers=req_headers)
            else:
                self._conn.request(self.get_request_method(),
                                   req.to_url(),
                                   headers=req_headers)

            response = self._conn.getresponse()

            if response.status != 200:
                self._logger.warning(
                    'Status: {} returned from twitter: {}'.format(
                        response.status, response.read()))
                return False
            else:
                self._logger.debug('Connected to Streaming API Successfully')

                # Clear any reconnects we had
                if self._rc_job is not None:
                    self._logger.error("We were reconnecting, now we're done!")
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
        self._rc_job = Job(self._run_stream,
                           self._rc_delay, False)
        self._rc_delay *= 2

    def get_request_method(self):
        return "GET"

    def _get_oauth_request(self, conn_url, request_params):
        """This function uses the oauthCreds passed from the transducer to
        sign the request.
        """
        request_params['oauth_version'] = '1.0'
        request_params['oauth_nonce'] = oauth.generate_nonce()
        request_params['oauth_timestamp'] = int(time.time())

        req = oauth.Request(method=self.get_request_method(),
                            url=conn_url,
                            parameters=request_params)

        req.sign_request(
            signature_method=oauth.SignatureMethod_HMAC_SHA1(),
            consumer=oauth.Consumer(
                self.creds.consumer_key, self.creds.app_secret),
            token=oauth.Token(
                self.creds.oauth_token, self.creds.oauth_token_secret)
        )

        return req

    def _record_line(self, line):
        """ Decode the line and add it to the end of the list """
        try:
            # reset the last received timestamp
            self._last_rcv = datetime.utcnow()

            data = json.loads(line.decode('utf-8'))
            # don't output 'limit' and 'delete' messages from twitter as
            # signals. For now just ignore them. When we have multiple block
            # outputs, they will be notified on different outputs.
            for msg in PUB_STREAM_MSGS:
                if data and msg in data:

                    # Log something about the message
                    report = "{} notice".format(PUB_STREAM_MSGS[msg])
                    if msg == "disconnect":
                        error_idx = int(data['disconnect']['code']) - 1
                        report += ": {}".format(DISCONNECT_REASONS[error_idx])
                    elif msg == "warning":
                        report += ": {}".format(data['message'])
                    self._logger.debug(report)

                    # Calculate total limit for limit signals
                    if msg == "limit":
                        # lock when calculating limit
                        with self._get_result_lock('limit'):
                            self._calculate_limit(data)

                    # Add a signal to the appropriate list
                    with self._get_result_lock(msg):
                        self._result_signals[msg].append(Signal(data))

                    return

            # If we didn't return yet, the message is a regular tweet.
            self._logger.debug("It's a tweet!")
            data = self.filter_results(data)
            if data:
                with self._get_result_lock('default'):
                    self._result_signals['default'].append(Signal(data))

        except Exception as e:
            self._logger.error("Could not parse line: %s" % str(e))

    def _get_result_lock(self, key):
        with self._lock_lock:
            return self._result_lock[key]

    def _calculate_limit(self, data):
        """ Calculate total limit count for limit signals """
        track = data.get('limit', {}).get('track', 0)
        if track > self._limit_count:
            limit = track - self._limit_count
            self._limit_count = track
        else:
            limit = 0
        data['limit_count'] = limit


    def filter_results(self, data):
        return data

    def _notify_results(self):
        """Method to be called from the notify job, will notify any tweets
        that have been buffered by the block, then clear the buffer.

        """
        output_names = list(self._result_signals.keys())
        for output in output_names:
            with self._get_result_lock(output):
                signals = self._result_signals[output]
                if signals:
                    output_id = output \
                        if output in ['default', 'limit'] else 'other'
                    self.notify_signals(signals, output_id)
                    self._result_signals[output] = []

    def _monitor_connection(self):
        """ Scheduled to run every self.rc_interval. Makes sure that some
        data has been received in the last self.rc_interval.

        """
        current_time = datetime.utcnow()
        time_since_data = current_time - self._last_rcv
        if time_since_data > self.rc_interval:
            self._logger.warning("No data received, we might be disconnected")
            self._setup_reconnect_attempt()

    def _authorize(self):
        """ Prepare the OAuth handshake and verify.

        """
        try:
            auth = OAuth1(self.creds.consumer_key,
                          self.creds.app_secret,
                          self.creds.oauth_token,
                          self.creds.oauth_token_secret)
            resp = requests.get(self.verify_url, auth=auth)
            if resp.status_code != 200:
                raise Exception("Status %s" % resp.status_code)
        except Exception:
            self._logger.error("Authentication Failed for consumer key: %s" %
                               self.creds.consumer_key)
