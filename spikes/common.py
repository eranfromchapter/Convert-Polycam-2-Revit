"""Shared constants for the Phase-0 spikes.

Reference project (already half-populated, safe for probes by prior agreement):
deal "1500 Bay Rd #1462". Its Plans and 3D already holds RoomData.csv and a
notes .txt — S6 must skip them, not duplicate them.

⚠️ Every uploaded probe file is PERMANENT (no delete tool). Names all start
with '_probe_' so the dev can sweep them from SharePoint in one pass, together
with the 4 pre-existing junk files (_typetest.dxf, _urltest.dxf, _keytest.dxf,
_polycamtest.jpg).
"""

REFERENCE_DEAL_ID = "db3a0a03-32fd-4a26-8aef-a644e93563ad"
REFERENCE_ADDRESS = "1500 Bay Rd #1462"
REFERENCE_CAPTURE_ID = "E4E8C071-9901-4F51-81A7-3D89B665E391"
PLANS_FOLDER = "Plans and 3D"
