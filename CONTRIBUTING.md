# Contributing to StateBreak

StateBreak welcomes small, reproducible contributions. The best first contribution is a scenario that demonstrates one state conflict using synthetic local data and a deterministic oracle.

Before opening a pull request, run `make check`. A scenario contribution must include its YAML/JSON file, expected naive and guarded behavior, a test, a short explanation, and a sanitized golden semantic report. An adapter contribution must implement the public protocol without adding a framework dependency to the core package.

Please keep issues specific: identify the observation, injected fault, action, authoritative final state, claim, and oracle verdict. Do not attach production prompts, credentials, customer data, or private URLs.
