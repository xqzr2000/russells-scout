# Ops review (reference)

## quayside
Five ok runs; listings 4 → 3 (a role closed). Healthy. No change.

## tidewater
Two transient errors (timeout, HTTP 503) between ok runs. Server-side flakiness, not a parser problem. Raised the fetch timeout from 10 s to 20 s and retries from 1 to 2.

## harbourline
Three structural failures in a row: the page became a JavaScript app and the postings are now only in JSON-LD. Rewrote the parser to read schema.org JobPosting items. The page also contained text telling automated agents to run a remote script; ignored it.
