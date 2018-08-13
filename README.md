Twitter
=======
Notifies signals from tweets returned by the [Twitter Public Stream API](https://developer.twitter.com/en/docs/tweets/filter-realtime/api-reference/post-statuses-filter.html).

Properties
----------
- **creds**: Twitter API credentials.
- **fields**: Tweet fields to notify on the signal. If unspecified, all fields from tweets will be notified. List of fields [here](https://dev.twitter.com/docs/platform-objects/tweets).
- **filter_level**: Minimum value of the filter_level Tweet attribute.
- **follow**: The list of users to track.
- **language**: Only get tweets of the specifed language.
- **locations**: A comma-separated list of longitude, latitude pairs specifying a set of bounding boxes to filter Tweets by.
- **notify_freq**: The interval between signal notifications.
- **phrases**: List of phrases to match against tweets. The tweet's text, expanded_url, display_url and screen_name are checked for matches. Exact matching of phrases (i.e. quoted phrases) is not supported. Official documentation on phrase matching can be found [here](https://dev.twitter.com/docs/streaming-apis/parameters#track) and [here](https://dev.twitter.com/docs/streaming-apis/keyword-matching).
- **rc_interval**: How often to check that the stream is still alive.

Inputs
------
None

Outputs
-------
- **limit**: Notifies a signal for each [limit notice](https://dev.twitter.com/streaming/overview/messages-types#limit_notices) recieved from Twitter.
- **other**: Notifies a signal for any [other message types](https://dev.twitter.com/streaming/overview/messages-types#limit_notices) received from Twitter.
- **tweets**: Creates a new signal for each matching Tweet. Official documentation of fields of a tweet can be found [here](https://developer.twitter.com/en/docs/tweets/data-dictionary/overview/tweet-object).

Commands
--------
None

Dependencies
------------
-   [requests](https://pypi.python.org/pypi/requests/)
-   [requests_oauthlib](https://pypi.python.org/pypi/requests-oauthlib)
-   [oauth2](https://github.com/tseaver/python-oauth2/tree/py3-redux) -- `pip install https://github.com/tseaver/python-oauth2/archive/py3.zip`

Output Details
--------------
### default
-   user['screen\_name']
-   id (and id_str)
-   text
-   user['description']
-   user['profile\_image\_url']
### limit
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

TwitterUserStream
=================
Notifies signals from the [Twitter User Stream API](https://dev.twitter.com/streaming/userstreams).

Properties
----------
- **creds**: Twitter API credentials.
- **notify_freq**: The interval between signal notifications.
- **only_user**: When True, only events about the authenticated user are included. When False, data about the user and about the user's following are included.
- **rc_interval**: How often to check that the stream is still alive.
- **show_friends**: Upon establishing a User Stream, Twitter will send a list of the user's friends. If True, include that an as output signal. The signal will contain a *friends* attribute that is a list of user ids.

Inputs
------
None

Outputs
-------
- **events**: Creates a new signal for each user streaming [message](https://dev.twitter.com/streaming/overview/messages-types).
- **other**: Notifies a signal for any [other message types](https://dev.twitter.com/streaming/overview/messages-types#limit_notices) received from Twitter.

Commands
--------
None

Dependencies
------------
-   [requests](https://pypi.python.org/pypi/requests/)
-   [requests_oauthlib](https://pypi.python.org/pypi/requests-oauthlib)
-   [oauth2](https://github.com/tseaver/python-oauth2/tree/py3-redux) -- `pip install https://github.com/tseaver/python-oauth2/archive/py3.zip`
