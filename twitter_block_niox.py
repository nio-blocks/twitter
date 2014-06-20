import json
import re
import http.client
import oauth2 as oauth
import time
from ssl import SSLError
from datetime import timedelta
from nio.common.block.base import Block
from nio.common.discovery import Discoverable, DiscoverableType
from nio.metadata.properties.holder import PropertyHolder
from nio.metadata.properties.list import ListProperty
from nio.metadata.properties.timedelta import TimeDeltaProperty
from nio.metadata.properties.object import ObjectProperty
from nio.metadata.properties.string import StringProperty
from nio.modules.scheduler.imports import Job
from nio.common.signal.base import Signal
from nio.modules.threading.imports import Thread, Lock, Event, sleep


class TwitterDisconnectException(Exception):
    pass


class TwitterWarningException(Exception):
    pass


class TwitterTrackException(Exception):
    pass


class TwitterCreds(PropertyHolder):

    """ Property holder for Twitter OAuth credentials.

    """
    consumer_key = StringProperty(default=None)
    app_secret = StringProperty(default=None)
    oauth_token = StringProperty(default=None)
    oauth_token_secret = StringProperty(default=None)


class Tweet(Signal):

    """ A Twitter signal.

    Args:
        tweet (dict): A single tweet, JSON deserialized.

    """

    def __init__(self, tweet):
        for field in tweet:
            setattr(self, field, tweet[field])


@Discoverable(DiscoverableType.block)
class TwitterX(Block):

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
    creds = ObjectProperty(TwitterCreds)
    notify_freq = TimeDeltaProperty(default={"seconds": 2})

    def __init__(self):
        super().__init__()
        self._signalObjectList = []
        self._signalObjectListLock = Lock()
        self._connectionThread = None
        self._runScheduler = None

    def start(self):
        super().start()

        self._connectionThread = StreamingThread(
            logger=self._logger,
            publisher=self._gatherSignalObject,
            limitPublisher=self._gatherLimit,
            phrases=self.phrases,
            oauthCreds={
                'appId': self.creds.consumer_key,
                'appSecret': self.creds.app_secret,
                'token': self.creds.oauth_token,
                'tokenSecret': self.creds.oauth_token_secret,
                'version': '1.0'
            }
        )
        self._connectionThread.start()
        self._run_job = Job(
            self.run,
            self.notify_freq,
            True
        )

    def stop(self):
        try:
            if self._run_job:
                self._run_job.cancel()

            if self._connectionThread:
                self._connectionThread.stop()
                self._connectionThread.join()
        finally:
            super().stop()

    def _gatherSignalObject(self, signal_object):
        try:
            self._signalObjectListLock.acquire()
            self._signalObjectList.append(signal_object)
        finally:
            self._signalObjectListLock.release()

    def _gatherLimit(self, count, cumulativeCount):
        pass

    def run(self):
        try:
            self._signalObjectListLock.acquire()
            length = len(self._signalObjectList)
            if length:
                self._logger.debug(
                    'Notifying: %s twitter signal objects' % length)
                self.notify_signals(self._signalObjectList)
                self._signalObjectList = []
        finally:
            self._signalObjectListLock.release()


class StreamingThread(Thread):
    _streamingUrlMethod = 'POST'
    _streamingServer = 'stream.twitter.com'

    _streamingUrl = 'https://stream.twitter.com/1.1/statuses/filter.json'
    _knownMessagesToDiscard = ['scrub_geo',
                               'status_withheld',  'user_withheld', 'delete']

    def __init__(self, logger, publisher, limitPublisher, phrases, oauthCreds):
        super().__init__()

        self.logger = logger
        self.publisher = publisher

        # keep track of limit messages from twitter
        self.limitPublisher = limitPublisher
        self.prevLimitCount = 0

        self.phrases = phrases

        self.oauthCreds = oauthCreds

        self._stopEvent = Event()
        self._signalObjectList = []
        self._signalObjectListLock = Lock()
        self._processJobId = None
        self._processTweetsIntervalSeconds = 1

    def stop(self):
        # signal thread to stop
        self._stopEvent.set()

    def _processTweet(self, signal_object_as_str):
        try:
            signal_object = self._getTwitterSignalObjectFromDataString(
                signal_object_as_str)
            if signal_object is not None:
                self.publisher(signal_object)
        except TwitterDisconnectException as tde:
            self.logger.warning(
                'The Twitter Streaming connection '
                'has been closed by Twitter, details: {0}'.format(
                    str(tde)))
            # stop reading thread
            self.stop()
        except TwitterWarningException as twe:
            self.logger.warning(
                'The Twitter Streaming processing is '
                'falling behind, details: {0}'.format(str(twe)))
        except TwitterTrackException as tte:
            self.logger.warning(
                'Twitter published a limit message, '
                'details: {0}'.format(str(tte)))

            # figure out what the limit was
            res = re.match('Twitter Limit: ([0-9,]+)', str(tte))
            if res is not None:
                newCount = int(res.group(1).replace(',', ''))

                # compare to our previous limit
                if newCount > self.prevLimitCount:
                    countForSignalObject = newCount - self.prevLimitCount
                else:
                    countForSignalObject = newCount
                self.logger.debug(
                    'This is means {0} were withheld this time.'
                    .format(countForSignalObject))

                # reset the previous limit
                self.prevLimitCount = newCount

                # publish the limit count
                self.limitPublisher(countForSignalObject, self.prevLimitCount)

        except Exception as e:
            self.logger.warning('The Tweet {0} could not be processed, '
                                'details: {1}'.format(
                                    signal_object_as_str, str(e)))

    def _processTweets(self):
        try:
            self._signalObjectListLock.acquire()
            for signal_object_as_str in self._signalObjectList:
                self._processTweet(signal_object_as_str)
            # remove all processed signal objects
            self._signalObjectList = []
        finally:
            self._signalObjectListLock.release()

    def _saveTwitterSignalObject(self, signal_object_as_string):
        try:
            self._signalObjectListLock.acquire()
            self._signalObjectList.append(signal_object_as_string)
        finally:
            self._signalObjectListLock.release()

    def run(self):
        # open the connection to the streaming API
        resp = self._openConnection()

        if resp is None:
            # could not connect, die
            self.logger.error(
                'Unable to connect to twitter, streaming thread is closing')
            return

        if self._processJobId is None:
            self.logger.info('Creating process tweets scheduling')
            self._processJobId = Job(
                self._processTweets,
                timedelta(seconds=self._processTweetsIntervalSeconds),
                True)

        # we are going to stay connected as long as we don't get a stop
        # trigger (stopEvent) and as long as the http response is not closed
        # (from twitter)
        while not self._stopEvent.isSet() and not resp.isclosed():
            try:
                self._getNextTweet(resp)
            except Exception as e:
                self.logger.error('Error in Streaming Twitter : {0}'.format(e))

        # wait for a potential process tweets in progress, which will process
        # a twitter connection closure if received through tweet connection.
        sleep(self._processTweetsIntervalSeconds)
        if not self._stopEvent.isSet() and resp.isclosed():
            # the EP is still running, twitter must have closed connection
            self.logger.warning('Twitter connection closed, re-opening now')
            self.run()
        else:
            # the loop must have exited because the thread was stopped, clean
            # up
            self.logger.info('Done reading new tweets, closing connection')
            self._closeConnection()

        if self._processJobId is not None:
            self.logger.info('Cancelling process tweets scheduling')
            self._processJobId.cancel()
            self._processJobId = None

        self.logger.info('Exiting Streaming Thread')

    # This function will make an attempt to reading the next raw tweet as
    # string, save it and defer processing.
    def _getNextTweet(self, resp):
        receivedStr = ''
        d = None

        while d != '\r':
            d, length = self._readFromStream(resp, 1)
            if d is None or length == 0:
                # if we claim to have read but it has length 0, it means we've
                # been disconnected
                resp.close()
                return
            else:
                receivedStr += d

        try:
            length = int(receivedStr)
        except Exception as e:
            self.logger.debug(
                "Error converting length from entry: {0}, details:"
                "{1}".format(receivedStr, str(e)))
            return

        if length:
            receivedStr, length = self._readFromStream(resp, length)
            if receivedStr is None or length == 0:
                # if we claim to have read but it has length 0, it means we've
                # been disconnected
                resp.close()
                return
            # do not hold reading functionality, save it and let scheduler
            # process it.
            self._saveTwitterSignalObject(receivedStr)

    # Simple Function: Read if we can, return it, otherwise return None
    def _readFromStream(self, resp, count):
        read_count = 0
        tweet_buffer = ''
        while read_count < count:
            try:
                temp_buffer = resp.read(count - read_count).decode('utf-8')
                if temp_buffer is not None:
                    length = len(temp_buffer)
                    if length == 0:
                        # requested read returned 0, this is a closed
                        # connection symptom
                        return None, 0
                    # partial read was ok, add it to current results
                    tweet_buffer += temp_buffer
                    read_count += length
                else:
                    return None, 0
            except SSLError:
                # the read timed out, nothing was read
                return None, 0
            except Exception as e:
                self.logger.error('Error reading from stream : {0}'.format(e))
                return None, 0
        return tweet_buffer, read_count

    def _messageTypeAllowed(self, message):
        # if we disconnect because someone else is connecting dataString looks
        # like this {"disconnect":{"code":7,"stream_name":
        #       "Societalin-statuses93696","reason":"admin logout"}}
        if 'disconnect' in message:
            raise TwitterDisconnectException('Twitter Connection Closed: %s' %
                                             self._getPropertyFromSignalObject(
                                                 message,
                                                 'disconnect,reason'))
        elif 'warning' in message:
            raise TwitterWarningException('Twitter Warning: {0}'.format(
                self._getPropertyFromSignalObject(message, 'warning,message')))
        elif 'limit' in message:
            raise TwitterTrackException('Twitter Limit: {0}'.format(
                self._getPropertyFromSignalObject(message, 'limit,track')))
        else:
            allowed = True
            for key in self._knownMessagesToDiscard:
                if key in message:
                    allowed = False
                    break
            return allowed

    def _getTwitterSignalObjectFromDataString(self, dataString):
        if len(dataString.strip()) == 0:
            return None

        try:
            ev = json.loads(dataString)
        except ValueError:
            # not a valid json
            self.logger.warning(
                'Invalid JSON returned from twitter {0}'.format(dataString))
            return None

        if self._messageTypeAllowed(ev):
            fromUserName = self._getPropertyFromSignalObject(
                ev, 'user,screen_name')
            text = self._getPropertyFromSignalObject(ev, 'text')
            createdAt = self._getPropertyFromSignalObject(ev, 'created_at')
            # profileImageUrl = self._getPropertyFromSignalObject(
                # ev, 'user,profile_image_url')
            #id = self._getPropertyFromSignalObject(ev, 'id_str')
            #link = "http://twitter.com/statuses/{0}".format(id)

            # longitude = self._getPropertyFromSignalObject(
                # ev, 'coordinates,coordinates,0', False)
            # latitude = self._getPropertyFromSignalObject(
                # ev, 'coordinates,coordinates,1', False)
            # country = self._getPropertyFromSignalObject(
                # ev, 'place,country_code', False)
            # place = self._getPropertyFromSignalObject(
                # ev, 'place,full_name', False)
            #origin = self._getPropertyFromSignalObject(ev, 'user,location')

            # mediaUrl = self._getPropertyFromSignalObject(
                # ev, 'entities,media,0,media_url', False)
            # mediaUrlType = self._getPropertyFromSignalObject(
                # ev, 'entities,media,0,type', False)

            # make sure every required property was retrieved
            signal_object = Signal()
            setattr(signal_object, 'fromUserName', fromUserName)
            setattr(signal_object, 'text', text)
            setattr(signal_object, 'created_at', createdAt)
            return signal_object
        else:
            self.logger.debug(
                'Discarding twitter signal object, signal object type is not '
                'to be processed')

    # given any dict `signal object`, this function will return the property
    # given in `signalObjectProperties`
    # signalObjectProperties can be a comma separated list to dive deeper
    # into the signal object
    #
    # Example: for the signal object e = {'a':1, 'b':2,
    #                                       'spelled':{'a':'one', 'b':'two'} },
    #    _getPropertyFromSignalObject(e,'a') returns 1
    #    _getPropertyFromSignalObject(e,'spelled,b') returns 'two'
    #
    # This is useful for pulling out specific properties from huge JSON
    # decoded strings
    #    such as the ones returned by the Twitter API
    #
    def _getPropertyFromSignalObject(self, signal_object,
                                     signalObjectProperties, required=True):
        original_signal_object = signal_object
        try:
            for prop in signalObjectProperties.split(','):
                if prop in signal_object:
                    signal_object = signal_object[prop]
                elif prop.isdigit():
                    signal_object = signal_object[int(prop)]
                else:
                    signal_object = None
                    break
        except:
            if required:
                self.logger.warning('No property {0} found in {1}'.format(
                    signalObjectProperties, original_signal_object))
            return None

        return signal_object

    # This function will open a connection to the Twitter API and return
    # the response.
    #     Also handles all re-connect attempts in case of a failure
    #
    def _openConnection(self):
        resp = self._getConnectionResponseOauth()
        timeoutWait = 1

        # reconnect check code, this follows Twitter's standards
        while (resp is None and
               timeoutWait < 16 and
               not self._stopEvent.isSet()):
            self.logger.warning(
                'Connection to twitter failed, retrying in %s seconds' %
                timeoutWait)

            # wait for timeoutWait
            sleep(timeoutWait)

            # double our timeout for the next one
            timeoutWait *= 2

            resp = self._getConnectionResponseOauth()

        return resp

    # This function initiates an HTTPSConnection, signs it with OAuth, and
    # returns the response.
    #     Returns None on failure
    def _getConnectionResponseOauth(self):
        try:
            self._conn = http.client.HTTPSConnection(
                host=self._streamingServer,
                timeout=45)

            request_params = {
                # include twitter query parameters below
                'stall_warnings': 'true',
                'delimited': 'length'
            }

            # only include the "track" parameter if we are filtering
            if 'filter' in self._streamingUrl:
                request_params['track'] = ','.join(self.phrases)

            req_headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': '*/*'
            }

            # get the signed request with the proper oauth creds
            req = self._getOauthRequest(request_params)

            if self._streamingUrlMethod == 'POST':
                self._conn.request(self._streamingUrlMethod,
                                   self._streamingUrl,
                                   body=req.to_postdata(),
                                   headers=req_headers)
            else:
                self._conn.request(self._streamingUrlMethod,
                                   req.to_url(), headers=req_headers)
            response = self._conn.getresponse()

            if response.status != 200:
                self.logger.warning(
                    'Status:{0} returned from twitter'.format(response.status))
                return None
            else:
                self.logger.debug('Connected to Streaming API Successfully')

            return response

        except Exception as e:
            self.logger.error('Error opening connection : {0}'.format(e))
            return None

    # This function uses the oauthCreds passed from the transducer to sign the
    # request
    def _getOauthRequest(self, request_params):
        request_params['oauth_version'] = self.oauthCreds['version']
        request_params['oauth_nonce'] = oauth.generate_nonce()
        request_params['oauth_timestamp'] = int(time.time())

        req = oauth.Request(method=self._streamingUrlMethod,
                            url=str(self._streamingUrl),
                            parameters=request_params)

        req.sign_request(
            signature_method=oauth.SignatureMethod_HMAC_SHA1(),
            consumer=oauth.Consumer(
                self.oauthCreds['appId'], self.oauthCreds['appSecret']),
            token=oauth.Token(
                self.oauthCreds['token'], self.oauthCreds['tokenSecret'])
        )

        return req

    def _closeConnection(self):
        self._conn.close()
