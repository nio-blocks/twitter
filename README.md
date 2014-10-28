Twitter
=======

A colleciton of blocks that use the [Twitter Streaming API](https://dev.twitter.com/streaming/overview)

-   [Twitter](https://github.com/nio-blocks/twitter#twitter)
-   [TwitterUserStream](https://github.com/nio-blocks/twitter#twitter_user_stream)

***

Twitter
===========

Notifies signals from tweets returned by the [Twitter Public Stream API](https://dev.twitter.com/docs/api/1.1/post/statuses/filter).

Properties
--------------

-   **phrases**: List of phrases to match against tweets. The tweet's text, expanded\_url, display\_url and screen\_name are checked for matches. Exact matching of phrases (i.e. quoted phrases) is not supported. Official documentation on phrase matching can be found [here](https://dev.twitter.com/docs/streaming-apis/parameters#track) and [here](https://dev.twitter.com/docs/streaming-apis/keyword-matching).
-   **creds**: Twitter API credentials.
-   **rc_interval**: How often to check that the stream is still alive.
-   **notify_freq**: Frequency at which tweets are notified from the block.
-   **fields**: Tweet fields to notify on the signal. If unspecified, all fields from tweets will be notified. List of fields [here](https://dev.twitter.com/docs/platform-objects/tweets).

Dependencies
----------------

-   [requests](https://pypi.python.org/pypi/requests/)
-   [requests_oauthlib](https://pypi.python.org/pypi/requests-oauthlib)
-   [oauth2](https://github.com/tseaver/python-oauth2/tree/py3-redux)

Commands
----------------
None

Input
-------
None

Output
---------
Creates a new signal for each matching Tweet. Official documentation of fields of a tweet can be found [here](https://dev.twitter.com/docs/platform-objects/tweets). The following is a list of commonly include attributes, but note that not all will be included on every signal:

-   user['scree\_name']
-   id (and id_str)
-   text
-   user['description']
-   user['profile\_image\_url']

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
-   [oauth2](https://github.com/tseaver/python-oauth2/tree/py3-redux) -- You want the branch py3

Commands
----------------
None

Input
-------
None

Output
---------
Creates a new signal for each user streaming [message](https://dev.twitter.com/streaming/overview/messages-types).
