Twitter
=======

Notifies signals from tweets returned by the [Twitter Streaming API](https://dev.twitter.com/docs/api/1.1/post/statuses/filter).

Properties
--------------

-   **phrases**: List of phrases to match against tweets. The tweet's text, expanded\_url, display\_url and screen\_name are checked for matches. Exacting matching of phrases (i.e. quoted phrases) is not supported. Official documentation on phrases matching can be found [here](https://dev.twitter.com/docs/streaming-apis/parameters#track) and [here](https://dev.twitter.com/docs/streaming-apis/keyword-matching).
-   **creds**: Twitter API credentials.
-   **rc_interval**: How often to check that the stream is still alive.
-   **notify_freq**: Frequency at which tweets are notified from the block.
-   **fields**: Tweet fields to notify on the signal. If unspecified, all fields from tweets will be notified.

Dependencies
----------------

-   [requests](https://pypi.python.org/pypi/requests/)
-   [requests_oathlib](https://pypi.python.org/pypi/requests-oauthlib)
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
