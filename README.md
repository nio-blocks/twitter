Twitter
=======

A colleciton of blocks that use the [Twitter Streaming API](https://dev.twitter.com/streaming/overview)

-   [Twitter](https://github.com/nio-blocks/twitter#twitter)
-   [TwitterUserStream](https://github.com/nio-blocks/twitter#twitter_user_stream)

***

Twitter
=======

Notifies signals from tweets returned by the [Twitter Public Stream API](https://dev.twitter.com/docs/api/1.1/post/statuses/filter).

Properties
----------

-   **phrases**: List of phrases to match against tweets. The tweet's text, expanded\_url, display\_url and screen\_name are checked for matches. Exact matching of phrases (i.e. quoted phrases) is not supported. Official documentation on phrase matching can be found [here](https://dev.twitter.com/docs/streaming-apis/parameters#track) and [here](https://dev.twitter.com/docs/streaming-apis/keyword-matching).
-   **creds**: Twitter API credentials.
-   **rc_interval**: How often to check that the stream is still alive.
-   **notify_freq**: Frequency at which tweets are notified from the block.
-   **fields**: Tweet fields to notify on the signal. If unspecified, all fields from tweets will be notified. List of fields [here](https://dev.twitter.com/docs/platform-objects/tweets).

Dependencies
------------

-   [requests](https://pypi.python.org/pypi/requests/)
-   [requests_oauthlib](https://pypi.python.org/pypi/requests-oauthlib)
-   [oauth2](https://github.com/tseaver/python-oauth2/tree/py3-redux) -- `pip install https://github.com/tseaver/python-oauth2/archive/py3.zip`

Commands
--------
None

Input
-----
None

Output
------

### default
Creates a new signal for each matching Tweet. Official documentation of fields of a tweet can be found [here](https://dev.twitter.com/docs/platform-objects/tweets). The following is a list of commonly include attributes, but note that not all will be included on every signal:

-   user['screen\_name']
-   id (and id_str)
-   text
-   user['description']
-   user['profile\_image\_url']

### limit
Notifies a signal for each [limit notice](https://dev.twitter.com/streaming/overview/messages-types#limit_notices) recieved from Twitter.

-   count - Current amount limited
-   cumulative_count - Total amount limited since connected was made to Twitter.
-   limit - The raw message received from Twitter.

```
{
    "count": 42,
    "cumulative_count": 314,
    "limit": {
        "track": 314
    }
}
```

### other
Notifies a signal for any [other message types](https://dev.twitter.com/streaming/overview/messages-types#limit_notices) received from Twitter.

****

TwitterUserStream
===========

Notifies signals from the [Twitter User Stream API](https://dev.twitter.com/streaming/userstreams).

Properties
--------------

-   **only_user**: When True, only events about the authenticated user are included. When False, data about the user and about the user's following are included.
-   **show_friends**: Upon establishing a User Stream, Twitter will send a list of the user's friends. If True, include that an as output signal. The signal will contain a *friends* attribute that is a list of user ids.
-   **creds**: Twitter API credentials.
-   **rc_interval**: How often to check that the stream is still alive.
-   **notify_freq**: Frequency at which tweets are notified from the block.

Dependencies
----------------

-   [requests](https://pypi.python.org/pypi/requests/)
-   [requests_oauthlib](https://pypi.python.org/pypi/requests-oauthlib)
-   [oauth2](https://github.com/tseaver/python-oauth2/tree/py3-redux) -- `pip install https://github.com/tseaver/python-oauth2/archive/py3.zip`

Commands
----------------
None

Input
-------
None

Output
---------
Creates a new signal for each user streaming [message](https://dev.twitter.com/streaming/overview/messages-types).
