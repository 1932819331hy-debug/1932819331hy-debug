Somnia's service catalog currently omits Multicall3 even though its official network documentation lists deployments for both mainnet and Shannon testnet. This adds one listing that reuses the existing `!offer:multicall3` template, links the network documentation, and labels the two different deployment addresses explicitly.

Only one row in `listings/specific-networks/somnia/services.csv` changes. Existing records and canonical offers remain byte-for-byte unchanged.

Sources checked on 2026-10-23:
- https://docs.somnia.network/developer/network-info
- https://docs.somnia.network/developer/smart-contracts

Validation at `67820f9f2a89f3edd28bd8c40319135d19fdb957`: the pinned upstream `validate_csv.py`, `csv_to_json.py` and `validate.py` checks all pass with Python 3.12.14 and json-tools `830d5f05ae15954c43ef23f4085c83d4548d5431`. CSV column count, slug order and unchanged-row bytes also pass. This is local validation; hosted CI has not run.

The deployment claim is based on the official documentation; no live RPC bytecode check is claimed. Indexed open-PR searches found no equivalent Somnia Multicall3 listing; #2879 concerns Filecoin and #4544 adds WSOMI.

AI assistance was used for research, preparation and validation. Account-holder personal-verification checklist items remain unconfirmed. Reward eligibility and payment remain subject to maintainer review; no reward address has been supplied.

