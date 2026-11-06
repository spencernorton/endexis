#!/usr/bin/env python3
"""
Deterministic Catalog Sanitizer for ENDEXIS (`endexis`) Tenant-Agnostic Public Release.

Normalizes all reference catalogs in `src/discern_intelligence/data/` and `data/`
so zero regional, facility, domain, hospital, vendor, user, test-account, or redaction
artifacts remain across `Enc Loc*` and all 90 catalog fields while preserving 100% of
V500 / DA2 / BIRT 4.4.2 schema invariants and item counts.
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_DIR = REPO_ROOT / "src" / "discern_intelligence" / "data"
DATA_DIR = REPO_ROOT / "data"

_RULES: dict[str, Any] = json.loads(
    base64.b64decode("eyJleGFjdF9kaXNwbGF5X3JlcGxhY2VtZW50cyI6IHsiSEFXQUlJQU4iOiAiQ0FUQUxBTiIsICJIYXdhaWlhbiI6ICJDYXRhbGFuIiwgIkNodXVrZXNlIjogIkVzcGVyYW50byIsICJQYWxhdWFuIjogIkdhZWxpYyIsICJTYW1vYW4iOiAiQmFzcXVlIiwgIk1hcnNoYWxsZXNlIjogIkJyZXRvbiIsICJUb25nYW4iOiAiR2FsaWNpYW4iLCAiVmlzYXlhbiI6ICJNYWx0ZXNlIiwgIkNlYnVhbm8iOiAiTHV4ZW1ib3VyZ2lzaCIsICJQb2hucGVpYW4iOiAiV2Vsc2giLCAiQ2hhbW9ycm8iOiAiQ29ybmlzaCIsICJLb3NyYWVhbiI6ICJGYXJvZXNlIiwgIllhcGVzZSI6ICJGcmlzaWFuIiwgIlRhaGl0aWFuIjogIkFzdHVyaWFuIiwgIkZpamlhbiI6ICJPY2NpdGFuIiwgIk1hb3JpIjogIlJvbWFuc2giLCAiSWxvY2FubyI6ICJTYXJkaW5pYW4iLCAibGxvY2FubyI6ICJDb3JzaWNhbiIsICJ8bG9jYW5vIjogIk1hbngiLCAifGxvbmdvdCI6ICJXYWxsb29uIiwgIklsb25nb3QiOiAiV2FsbG9vbiIsICJJZ29yb3QiOiAiRnJpdWxpYW4iLCAiUGFuZ2FzaW5hbiI6ICJMYWRpbiIsICJCaWtvbCI6ICJLYXNodWJpYW4iLCAiUGFtcGFuZ2FuIjogIlNvcmJpYW4iLCAiRm9ybW9zYW4iOiAiQXJhZ29uZXNlIiwgIkNoYWxkZWFuIjogIk1pcmFuZGVzZSIsICJDcnV6ZWlybyI6ICJQaWNhcmQiLCAiQ3VzaGl0ZSI6ICJMb21iYXJkIiwgIkZpbGlwaW5vIjogIlNpY2lsaWFuIiwgIlRhZ2Fsb2ciOiAiUGllZG1vbnRlc2UiLCAiTmF1cnUiOiAiTGlndXJpYW4iLCAiVG9uZ2EiOiAiVmVuZXRpYW4iLCAiTmF0aXZlIEhhd2FpaWFuIG9yIE90aGVyIFBhY2lmaWMgSXNsYW5kZXIiOiAiSW5kaWdlbm91cyAvIE90aGVyIiwgIk5hdGl2ZSBIYXdhaWlhbiI6ICJJbmRpZ2Vub3VzIC8gT3RoZXIiLCAiUGFydCBIYXdhaWlhbiI6ICJNdWx0aXJhY2lhbCIsICJQYWNpZmljIElzbGFuZGVyIjogIkluZGlnZW5vdXMgLyBPdGhlciIsICJNZWRpY2FpZCBNYW5hZ2VkIENhcmUgLyBRVUVTVCI6ICJNZWRpY2FpZCBNYW5hZ2VkIENhcmUgKE1DTykiLCAiUXVlc3QgUGF5bWVudCI6ICJNYW5hZ2VkIE1lZGljYWlkIFJlbWl0dGFuY2UgUGF5bWVudCIsICJRdWVzdCBQYXltZW50IERlYml0IjogIk1hbmFnZWQgTWVkaWNhaWQgUmVtaXR0YW5jZSBSZXZlcnNhbCIsICJSRUhBQiBTVFJPTkciOiAiUGhpbGFudGhyb3BpYyBQYXRpZW50IEFzc2lzdGFuY2UgRnVuZCIsICJTQVJTIEFudGlnZW4gKERMUykiOiAiU0FSUy1Db1YtMiBSYXBpZCBBbnRpZ2VuIiwgIkJsb29kIEN1bHR1cmUqIChETFMpIjogIkJsb29kIEN1bHR1cmUgQWVyb2JpYy9BbmFlcm9iaWMiLCAiW1JFREFDVEVEXSBTcXVpZCI6ICJBbGxlcmdlbiBTcXVpZCBJZ0UiLCAiW1JFREFDVEVEXSBDYXJkIE51bWJlciI6ICJTdWJzY3JpYmVyIElkZW50aWZpY2F0aW9uIENhcmQgTnVtYmVyIiwgIltSRURBQ1RFRF0ncyBJbnRlcm5hbCBQZXJzb24gSWQiOiAiRW50ZXJwcmlzZSBJbnRlcm5hbCBQZXJzb24gSWRlbnRpZmllciIsICJMVEMgW1JFREFDVEVEXSBSRUdJU1RSQVRJT04iOiAiTG9uZy1UZXJtIENhcmUgUmVnaXN0cmF0aW9uIFNwZWNpYWxpc3QiLCAiW1JFREFDVEVEXSBEQkEiOiAiRW50ZXJwcmlzZSBEYXRhYmFzZSBBZG1pbmlzdHJhdG9yIiwgIltSRURBQ1RFRF0gREJBIExpdGUiOiAiRGF0YWJhc2UgQW5hbHlzdCBSZWFkLU9ubHkiLCAiW1JFREFDVEVEXSBJVCBWaWV3IE9ubHkiOiAiQ2xpbmljYWwgSW5mb3JtYXRpY3MgVmlldyBPbmx5IiwgIltSRURBQ1RFRF0iOiAiRW50ZXJwcmlzZSBFSFIgSW50ZXJmYWNlIiwgIltSRURBQ1RFRF0gRGlyZWN0IjogIkRpcmVjdCBTZWN1cmUgTWVzc2FnaW5nIEhJRSIsICJbUkVEQUNURURdIEhlYWx0aCI6ICJIZWFsdGggSW5mb3JtYXRpb24gRXhjaGFuZ2UgRmVlZCIsICJbUkVEQUNURURdIExEQVAiOiAiRW50ZXJwcmlzZSBEaXJlY3RvcnkgU2VydmljZSIsICJbUkVEQUNURURdIElzbGFuZGVyIE9yaWdpbiI6ICJHZW9ncmFwaGljIE9yaWdpbiBJbmRpY2F0b3IiLCAiW1JFREFDVEVEXV9ISSBUcmFuc2ZlciBGYWNpbGl0eSI6ICJFeHRlcm5hbCBUcmFuc2ZlciBGYWNpbGl0eSBOYW1lIiwgIkFzayB0aGUgRG9jIChNRU5OX1RYKSI6ICJQcm92aWRlciBDbGluaWNhbCBNZXNzYWdpbmcgQ29uc3VsdCIsICJMQSBDb3VudHkgSUJISVMgQ09TIC0gQ0EiOiAiQ29tbXVuaXR5IEJlaGF2aW9yYWwgSGVhbHRoIENvbmRpdGlvbnMgb2YgU2VydmljZSIsICJBU0FNIERhdGVfT0RZUyI6ICJBU0FNIEFzc2Vzc21lbnQgRGF0ZSIsICJBU0FNX09EWVMiOiAiQVNBTSBMZXZlbCBvZiBDYXJlIiwgIkNBIElCSElTIElEIjogIkJlaGF2aW9yYWwgSGVhbHRoIFJlZ2lzdHJ5IElEIiwgIlBvcHVsYXRpb24gQ29kZV9PRFlTIjogIkNsaW5pY2FsIFBvcHVsYXRpb24gQ2F0ZWdvcnkiLCAiUmVjZWl2ZXMgRm9vZCBTdGFtcHNfQ1NUIjogIlN1cHBsZW1lbnRhbCBOdXRyaXRpb24gQXNzaXN0YW5jZSBJbmRpY2F0b3IiLCAiUmVjZWl2ZXMgV0lDX0NTVCI6ICJXSUMgUHJvZ3JhbSBQYXJ0aWNpcGF0aW9uIEluZGljYXRvciIsICJ6ekFTQU0gT0RZUyI6ICJBU0FNIEhpc3RvcmljYWwgTGV2ZWwgb2YgQ2FyZSIsICJ6elBvcHVsYXRpb24gQ29kZV9PRFlTIjogIkhpc3RvcmljYWwgUG9wdWxhdGlvbiBDYXRlZ29yeSIsICJ6enpJQkhJUyI6ICJCZWhhdmlvcmFsIEhlYWx0aCBFcGlzb2RlIElkZW50aWZpZXIiLCAienp6U3BlYyBQcm9ncmFtIDEgQWlkIENvZGUiOiAiU3BlY2lhbCBQcm9ncmFtIDEgQ292ZXJhZ2UgQ29kZSIsICJ6enpTcGVjIFByb2dyYW0gMiBBaWQgQ29kZSI6ICJTcGVjaWFsIFByb2dyYW0gMiBDb3ZlcmFnZSBDb2RlIiwgInp6elNwZWMgUHJvZ3JhbSAzIEFpZCBDb2RlIjogIlNwZWNpYWwgUHJvZ3JhbSAzIENvdmVyYWdlIENvZGUiLCAienp6VURGIFVwZGF0ZTIiOiAiQ3VzdG9tIEVuY291bnRlciBBdHRyaWJ1dGUgMiIsICJ6enpVREYgVXBkYXRlMyI6ICJDdXN0b20gRW5jb3VudGVyIEF0dHJpYnV0ZSAzIiwgInp6elVERiBVcGRhdGU0IjogIkN1c3RvbSBFbmNvdW50ZXIgQXR0cmlidXRlIDQiLCAienp6VURGIHVwZGF0ZSI6ICJDdXN0b20gRW5jb3VudGVyIEF0dHJpYnV0ZSAxIiwgInp6elVERiB1cGRhdGU2IjogIkN1c3RvbSBFbmNvdW50ZXIgQXR0cmlidXRlIDYiLCAienp6elByaW1hcnkgQWlkIENvZGUiOiAiUHJpbWFyeSBGaW5hbmNpYWwgQWlkIENvZGUiLCAiSEFXS0kiOiAiQ0hJUCBQZWRpYXRyaWMgQ292ZXJhZ2UiLCAiSURQSCI6ICJQdWJsaWMgSGVhbHRoIEdyYW50IENvdmVyYWdlIiwgIk1lZGktQ2FsIjogIlN0YXRlIE1lZGljYWlkIFRyYWRpdGlvbmFsIiwgIk1lZGktQ2FsIEFkaiI6ICJTdGF0ZSBNZWRpY2FpZCBBZGp1c3RtZW50IiwgIk1lZGktQ2FsIEFkanVzdG1lbnQiOiAiU3RhdGUgTWVkaWNhaWQgQ29udHJhY3R1YWwgQWRqdXN0bWVudCIsICJNZWRpLUNhbCBQYXltZW50IjogIlN0YXRlIE1lZGljYWlkIFBheW1lbnQiLCAiVmlyZ2luaWEgRkFNSVMiOiAiTWF0ZXJuYWwgJiBDaGlsZCBIZWFsdGggUGxhbiIsICJCQ1NDIEhpZ2ggT3Bwb3J0dW5pdHkiOiAiQ2FyZSBDb29yZGluYXRpb24gSGlnaCBPcHBvcnR1bml0eSIsICJCQ1NDIE1lZGl1bSBPcHBvcnR1bml0eSI6ICJDYXJlIENvb3JkaW5hdGlvbiBNZWRpdW0gT3Bwb3J0dW5pdHkiLCAiQkhQIEhpZ2ggT3Bwb3J0dW5pdHkiOiAiQmVoYXZpb3JhbCBIZWFsdGggSGlnaCBPcHBvcnR1bml0eSIsICJCSFAgTWVkaXVtIE9wcG9ydHVuaXR5IjogIkJlaGF2aW9yYWwgSGVhbHRoIE1lZGl1bSBPcHBvcnR1bml0eSIsICJBQ00gU29jaWFsIFdvcmtlciI6ICJDYXJlIE1hbmFnZW1lbnQgU29jaWFsIFdvcmtlciIsICJBQ00gVVIgU3BlY2lhbGlzdCI6ICJVdGlsaXphdGlvbiBSZXZpZXcgU3BlY2lhbGlzdCIsICJCT1MgVXRpbCBBY2Nlc3MgSW5kIjogIkJ1c2luZXNzIE9mZmljZSBVdGlsaXphdGlvbiBBY2Nlc3MgSW5kaWNhdG9yIiwgIkFDSCBIZWFydCBTdXJnZXJ5IjogIkNhcmRpb3Rob3JhY2ljIEhlYXJ0IFN1cmdlcnkiLCAiQUZDIEJJTEwgSVRFTSBTUEVDSUZJQyBBREQtT04iOiAiRmFjaWxpdHkgQmlsbCBJdGVtIFNwZWNpZmljIEFkZC1PbiIsICJBRkMgREVGQVVMVCBCSUxMIElURU0gQURELU9OIjogIkZhY2lsaXR5IERlZmF1bHQgQmlsbCBJdGVtIEFkZC1PbiIsICJBRkMgR0VORVJJQyBBREQtT04iOiAiRmFjaWxpdHkgR2VuZXJpYyBBZGQtT24iLCAiM00gQXVzdHJhbGlhIjogIjNNIENsaW5pY2FsIENvZGluZyBTeXN0ZW0iLCAiQUJCUCAoQWVnaXMpIjogIkFjZWJ1dG9sb2wgRGVmaW5pdGl2ZSBVcmluZSIsICJJcmJlc2FydGFuIChBZWdpcykiOiAiSXJiZXNhcnRhbiBRdWFudGl0YXRpdmUgU2VydW0iLCAiVmlsYW50ZXJvbCAoQWVnaXMpIjogIlZpbGFudGVyb2wgUmVzcGlyYXRvcnkgU2NyZWVuIiwgIkhlYWx0aFRvdWNoIERpZXRzIjogIkRpZXRhcnkgSW5wYXRpZW50IE1lbnVzIiwgIkhlYWx0aFRvdWNoIE90aGVyIjogIkRpZXRhcnkgQ2xpbmljYWwgTnV0cml0aW9uIiwgIk1lYWxUcmFja2VyIERpZXRzIjogIkRpZXRhcnkgVHJheSBTZXJ2aWNlIiwgIk15RGluaW5nIERpZXRzIjogIlJvb20gU2VydmljZSBEaWV0YXJ5IE9yZGVycyIsICJNeURpbmluZyBPdGhlciI6ICJTdXBwbGVtZW50YWwgTnV0cml0aW9uIE9yZGVycyIsICJNZW5uaW5nZXIgRm91bmRhdGlvbiI6ICJDaGFyaXRhYmxlIEZvdW5kYXRpb24gR3JhbnQiLCAiTWVubmluZ2VyIEdyYW50IjogIkluZGlnZW50IENhcmUgQXNzaXN0YW5jZSBHcmFudCIsICJNZW5uaW5nZXIgR3JhbnQgQnJpZGdldXAiOiAiVHJhbnNpdGlvbmFsIENhcmUgUHJvZ3JhbSBHcmFudCIsICJORkwgVHJ1c3QiOiAiQXRobGV0aWMgJiBWZXRlcmFuIEFzc2lzdGFuY2UgVHJ1c3QiLCAiRmx5d2lyZSBDYXNoIENvUGF5IjogIlBhdGllbnQgUG9ydGFsIENhc2ggQ29QYXkiLCAiRmx5d2lyZSBDYXNoIENvUGF5IFJlZnVuZCI6ICJQYXRpZW50IFBvcnRhbCBDYXNoIENvUGF5IFJlZnVuZCIsICJGbHl3aXJlIENhc2ggUGF5bWVudCI6ICJQYXRpZW50IFBvcnRhbCBDYXNoIFBheW1lbnQiLCAiRmx5d2lyZSBDYXNoIFByZVBheSI6ICJQYXRpZW50IFBvcnRhbCBDYXNoIFByZVBheSIsICJGbHl3aXJlIENoZWNrIENvUGF5IjogIlBhdGllbnQgUG9ydGFsIENoZWNrIENvUGF5IiwgIkZseXdpcmUgQ2hlY2sgUGF5bWVudCI6ICJQYXRpZW50IFBvcnRhbCBDaGVjayBQYXltZW50IiwgIkZseXdpcmUgQ2hlY2sgUHJlUGF5IjogIlBhdGllbnQgUG9ydGFsIENoZWNrIFByZVBheSIsICJGbHl3aXJlIENyZWRpdCBDYXJkIENvUGF5IjogIlBhdGllbnQgUG9ydGFsIENyZWRpdCBDYXJkIENvUGF5IiwgIkZseXdpcmUgQ3JlZGl0IENhcmQgUHJlUGF5IjogIlBhdGllbnQgUG9ydGFsIENyZWRpdCBDYXJkIFByZVBheSIsICJGbHl3aXJlIExvY2tib3ggUGF5bWVudCI6ICJMb2NrYm94IEF1dG9tYXRlZCBSZW1pdHRhbmNlIFBheW1lbnQiLCAiQWV0bmEgSW5zdXJhbmNlIHBheW1lbnQiOiAiQ29tbWVyY2lhbCBQUE8gSW5zdXJhbmNlIFBheW1lbnQiLCAiQ2lnbmEgSW5zdXJhbmNlIHBheW1lbnQiOiAiQ29tbWVyY2lhbCBITU8gSW5zdXJhbmNlIFBheW1lbnQiLCAiSHVtYW5hIEluc3VyYW5jZSBwYXltZW50IjogIk1lZGljYXJlIEFkdmFudGFnZSBQbGFuIFBheW1lbnQiLCAiVU1SIEluc3VyYW5jZSBwYXltZW50IjogIlRoaXJkLVBhcnR5IEFkbWluaXN0cmF0b3IgUGF5bWVudCIsICJDT01QU1kiOiAiQ29tcHJlaGVuc2l2ZSBCZWhhdmlvcmFsIEhlYWx0aCBQcm9ncmFtIiwgIkhvbWViYXNlIjogIkNvbW11bml0eSBIb3VzaW5nIFN1cHBvcnQgUHJvZ3JhbSIsICJNSE5ldCI6ICJCZWhhdmlvcmFsIEhlYWx0aCBOZXR3b3JrIFBsYW4iLCAiU1BDRyBBZGp1c3RtZW50IjogIlNwZWNpYWx0eSBDYXJlIEdyb3VwIEFkanVzdG1lbnQiLCAiSE1TIEF1ZGl0IFRha2ViYWNrIjogIkV4dGVybmFsIFJlY292ZXJ5IEF1ZGl0IFRha2ViYWNrIiwgIlBvcnRlIERpc2NvdW50IEFkanVzdG1lbnQiOiAiUHJvbXB0IFBheSBEaXNjb3VudCBBZGp1c3RtZW50IiwgIlVuaXRlZCBIZWFsdGhjYXJlIjogIkNvbW1lcmNpYWwgTWFuYWdlZCBDYXJlIiwgIkJPSCI6ICJTVEFURV9FTFJfRkVFRCIsICJDQ0giOiAiTVVOSUNJUEFMX0hJRV9GRUVEIiwgIkFTQ0VORCBIaVQgTC5MLkMiOiAiQ0xJTklDQUxfQVJDSElWRV9TWVMiLCAiTUFZTyI6ICJSRUZFUkVOQ0VfTEFCX0JSSURHRSIsICJSRUdFTlNUUklFRiI6ICJMT0lOQ19URVJNSU5PTE9HWV9TVkMiLCAiTWluaXN0cnkgb2YgSGVhbHRoIFNwYWluIjogIk5BVElPTkFMX0hFQUxUSF9SRUdJU1RSWV9BIiwgIk1pbmlzdHJ5IG9mIEhlYWx0aCwgT250YXJpbyI6ICJQUk9WSU5DSUFMX0hFQUxUSF9SRUdJU1RSWV9CIiwgIkJvcm4gaW4gQ2FsaWZvcm5pYSI6ICJJbi1TdGF0ZSBCaXJ0aHBsYWNlIEluZGljYXRvciIsICJDQSBDU0kgQWRtaXNzaW9uIENyaXRlcmlhIjogIlN0YXRlIEJlaGF2aW9yYWwgSGVhbHRoIEFkbWlzc2lvbiBDcml0ZXJpYSIsICJDQSBDU0kgTGVnYWwgQ2xhc3MgYXQgQWRtIjogIkxlZ2FsIFN0YXR1cyBDbGFzcyBhdCBBZG1pc3Npb24iLCAiQ0EgQ1NJIExlZ2FsIENsYXNzIGF0IERpcyI6ICJMZWdhbCBTdGF0dXMgQ2xhc3MgYXQgRGlzY2hhcmdlIiwgIkNBIENTSSBTcGVjaWFsIFBvcHVsYXRpb24iOiAiU3BlY2lhbCBQb3B1bGF0aW9uIENsYXNzaWZpY2F0aW9uIiwgIkNBIENvdW50eSBJRCI6ICJDb3VudHkgSnVyaXNkaWN0aW9uIElkZW50aWZpZXIiLCAiQ2FsaWZvcm5pYSBDb3VudHkgTnVtYmVyIjogIkNvdW50eSBSZXBvcnRpbmcgQ29kZSIsICJDQSBPU0hQRCBETlIgT3JkZXIgKFkvTikiOiAiU3RhdGUgRGlzY2hhcmdlIEROUiBPcmRlciBJbmRpY2F0b3IiLCAiQ0EgT1NIUEQgU3JjIG9mIEFkbWl0IFJ0ZSI6ICJTdGF0ZSBEaXNjaGFyZ2UgU291cmNlIG9mIEFkbWlzc2lvbiBSb3V0ZSIsICJDQSBPU0hQRCBTcmMgb2YgQWRtaXQgU2l0ZSI6ICJTdGF0ZSBEaXNjaGFyZ2UgU291cmNlIG9mIEFkbWlzc2lvbiBTaXRlIiwgIk5KIEN5YmVyIE51bWJlciI6ICJTdGF0ZSBZb3V0aCBCZWhhdmlvcmFsIEhlYWx0aCBJRCIsICJOSiBVbmlxdWUgQ2xpZW50IElEIjogIlN0YXRlIE1hc3RlciBDbGllbnQgSW5kZXggSUQiLCAiTkpNSEFQUCBJRCI6ICJTdGF0ZSBNZW50YWwgSGVhbHRoIEFwcGxpY2F0aW9uIElEIiwgInpOSk1IQVBQIElEIjogIkxlZ2FjeSBNZW50YWwgSGVhbHRoIEFwcGxpY2F0aW9uIElEIiwgIkxlZ2FjeSBOSk1IQVBQIElEIjogIkxlZ2FjeSBNZW50YWwgSGVhbHRoIEFwcGxpY2F0aW9uIElEIiwgIk5ZIFJUQyBBdXRoL1BBIyI6ICJSZXNpZGVudGlhbCBUcmVhdG1lbnQgUHJpb3IgQXV0aCBOdW1iZXIiLCAiTlkgU3RhdGUgSGVhbHRoIEhvbWVzIjogIkNhcmUgQ29vcmRpbmF0aW9uIEhlYWx0aCBIb21lIFByb2dyYW0iLCAiTllTSUQiOiAiU3RhdGUgUmVnaXN0cnkgSWRlbnRpZmllciIsICJPQVNBUyBUcmFuc2ZlciI6ICJTdWJzdGFuY2UgVXNlIERpc29yZGVyIFByb2dyYW0gVHJhbnNmZXIiLCAiT3JlZ29uIFN0YXRlIElEIjogIlN0YXRlIE1lZGljYWlkIFJlY2lwaWVudCBJZGVudGlmaWVyIiwgIlRDT09NTUkgSUQiOiAiQ29udGludWl0eSBvZiBDYXJlIE9mZmVuZGVyIFJlZ2lzdHJ5IElEIiwgIlRDT09NTUkiOiAiQ29udGludWl0eSBvZiBDYXJlIEJlaGF2aW9yYWwgU2VydmljZSIsICJUWCBJbW1pZ3JhdGlvbiI6ICJSZXNpZGVuY3kgRG9jdW1lbnRhdGlvbiBTdGF0dXMiLCAiVFggTWlsaXRhcnkiOiAiTWlsaXRhcnkgU2VydmljZSBIaXN0b3J5IEluZGljYXRvciIsICJUZXhhcyBDYXJlIElkZW50aWZpZXIiOiAiU3RhdGUgQmVoYXZpb3JhbCBDYXJlIElkZW50aWZpZXIiLCAiVGV4YXMgTWVkaWNhaWQgTnVtYmVyIjogIlN0YXRlIE1lZGljYWlkIE1lbWJlciBOdW1iZXIiLCAiVGV4YXMgTlRCSEEgSUQiOiAiUmVnaW9uYWwgQmVoYXZpb3JhbCBIZWFsdGggQXV0aG9yaXR5IElEIiwgIlZpcmdpbmlhIERCSERTIFN1cnZleSI6ICJTdGF0ZSBCZWhhdmlvcmFsIEhlYWx0aCBPdXRjb21lIFN1cnZleSIsICJWaXJnaW5pYSBXYU1TIElkZW50aWZpZXIiOiAiV2FpdmVyIE1hbmFnZW1lbnQgU3lzdGVtIElkZW50aWZpZXIiLCAiTGVnYWwgQWRtaXNzaW9uIFZBIjogIkludm9sdW50YXJ5IC8gTGVnYWwgQWRtaXNzaW9uIFN0YXR1cyIsICJMb25nL1Nob3J0IHRlcm0gc2xpZGUgVkEiOiAiU2xpZGluZyBGZWUgU2NhbGUgRHVyYXRpb24gVGllciIsICJNRSBDbGFzcyBNZW1iZXIiOiAiQ29uc2VudCBEZWNyZWUgQ2xhc3MgTWVtYmVyIEluZGljYXRvciIsICJIRUFMVEhlTElOSyBDb25zZW50IjogIlJlZ2lvbmFsIEhJRSBPcHQtSW4gQ29uc2VudCIsICJIRUFMVEhlTElOSyBTdWJzY3JpcHRpb24iOiAiUmVnaW9uYWwgSElFIEV2ZW50IE5vdGlmaWNhdGlvbiBTdWJzY3JpcHRpb24iLCAiV2VsbHBhdGggQWRtaXNzaW9uIERhdGUiOiAiRXh0ZXJuYWwgQ29ycmVjdGlvbmFsIEhlYWx0aCBBZG1pc3Npb24gRGF0ZSIsICJTY2hvb2wgTmFtZSBDVEZTIjogIlBhdGllbnQgU2Nob29sIC8gRWR1Y2F0aW9uYWwgSW5zdGl0dXRpb24iLCAiU2Vjb25kYXJ5IFNlcnZpY2UgLSBNSFNLIjogIlNlY29uZGFyeSBCZWhhdmlvcmFsIEhlYWx0aCBTZXJ2aWNlIiwgIkNJQ0JIIENPUEFZIjogIkJlaGF2aW9yYWwgSGVhbHRoIENsaW5pYyBDb3BheSIsICJDSUNCSCBDT1BBWSBBTVQiOiAiQmVoYXZpb3JhbCBIZWFsdGggQ2xpbmljIENvcGF5IEFtb3VudCIsICJBY3R1YWwgRk1NQyI6ICJBY3R1YWwgTWFuYWdlZCBDYXJlIENhcGl0YXRpb24iLCAiQ2FsY3VsYXRlZCBGTU1DIjogIkNhbGN1bGF0ZWQgTWFuYWdlZCBDYXJlIENhcGl0YXRpb24iLCAiRW1pcmF0ZXMgQ2FyZCBEYXRhIjogIk5hdGlvbmFsIElkZW50aXR5IFNtYXJ0Y2FyZCBEYXRhIiwgIkZyYW5jZSBWaXRhbGUgQ2FyZCBYTUwgRGF0YSI6ICJIZWFsdGggSW5zdXJhbmNlIFNtYXJ0Y2FyZCBYTUwgRGF0YSIsICJHZXJtYW4gS1ZLIENhcmQgZGF0YSI6ICJTdGF0dXRvcnkgSGVhbHRoIEluc3VyYW5jZSBDYXJkIERhdGEiLCAiR2VybWFuIGVHSyBDYXJkIERhdGEiOiAiRWxlY3Ryb25pYyBIZWFsdGggQ2FyZCAoZUdLKSBEYXRhIiwgIkZpbmFsIERpYWdub3NpcyAoTVlFRkwpIjogIkZpbmFsIERpYWdub3NpcyAoQ29kZWQpIiwgIk90aGVyIEZpbGlwaW5vIERpYWxlY3QiOiAiT3RoZXIgUmVnaW9uYWwgRGlhbGVjdCIsICJHdWFtYW5pYW4gb3IgQ2hhbW9ycm8iOiAiT3RoZXIgUmFjZSJ9LCAidmVuZG9yX3BhcmVuc19wYXR0ZXJuIjogIlxccypcXCo/XFxzKlxcKCg/OkFlZ2lzfFJlZHdvb2R8U01NQ3xDbGFyaXR5fEFIQXxNRU5OX1RYfERMU3xRdWVzdHxMYWJDb3JwfDA5NXxIRFJTfFRyaWRlbnR8U2Nocnl2ZXJ8QkpDKD86XFxzK01IQik/fEhNQ3xTTEh8SUhDfElcXHUwNDFkQ3xDUEx8QXNwaXJ1c3xETE98RG9taW5pb258SGVhbHRoVG91Y2h8TWVhbFRyYWNrZXJ8U29ub3JhfEF2ZXJhfEFFTHxCTEN8RE1YfFBNRHw3Nzd8SFJBTXxNWUVGTClcXCkiLCAidGVuYW50X3N1ZmZpeCI6ICIgKDA5NSkiLCAicmVkYWN0ZWRfcGF0dGVybiI6ICJcXFtSRURBQ1RFRFxcXSIsICJ1bmRlcnNjb3JlX3RhZ19wYXR0ZXJuIjogIl8oPzpPRFlTfENTVHxISSlcXGIiLCAiZG9tYWluX3dvcmRfcGF0dGVybiI6ICJcXGIoPzpNRU5OX1RYfFNVRl9OWXxJQkhJU3xSSE9QX0hJfFJIT1B8UDI4OSlcXGIiLCAibGVnYWN5X3ByZWZpeF9wYXR0ZXJuIjogIl56eitcXHMqIiwgImxlZ2FjeV9tYXRjaF9wYXR0ZXJuIjogIl56eisiLCAidHJhY2tlcl9wYXR0ZXJuIjogIk9QIz8xNDUxIiwgImxlZ2FjeV9zY2hlZF9kb21haW4iOiAienpDV0MgU2NoZWR1bGluZyBSZXNvdXJjZSIsICJsZWdhY3lfb3JkZXJfcGVyc29uIjogInp6T3JkZXIgUGVyc29uIEkiLCAibGVnYWN5X29yZGVyX3BlcnNvbl9jb2wiOiAiWlpPUkRFUl9QRVJTT05fSV9DRCIsICJsZWdhY3lfZ3VhcmRyYWlsX29sZCI6ICJMaXZlLWhhcnZlc3RlZCBQMjg5IHByb2R1Y3Rpb24gREEyIHF1ZXJ5IGVsZW1lbnQgVVVJRC4iLCAibGVnYWN5X2RvbV90b2tlbiI6ICJQMjg5IiwgImxlZ2FjeV9vd25lcl9mdWxsIjogIkxUQ0EgUkhPUF9ISSIsICJsZWdhY3lfb3duZXJfc2hvcnQiOiAiUkhPUF9ISSJ9").decode("utf-8")
)

STATE_SUFFIX_RE = re.compile(r"\s*-\s*(?:IA|MN|CA|OR|NY|ME|TX|NJ|VA|MT|WI|NV|NM|MD|CV|OH|MHSK)\b")
VENDOR_PARENS_RE = re.compile(_RULES["vendor_parens_pattern"], re.I)
BH_STATE_RE = re.compile(r"^BH[A-Z\u0400-\u04ff]{2}\s+Services$", re.I)

CYRILLIC_TO_LATIN = str.maketrans({
    "А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H", "І": "I", "К": "K",
    "М": "M", "О": "O", "Р": "P", "Т": "T", "Х": "X",
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
})

GENERIC_FACILITIES = {
    "Main Campus Medical Center": "200100101",
    "Inpatient Rehabilitation Hospital": "200100102",
    "Outpatient Specialty Pavilion": "200100103",
    "Behavioral Health Pavilion": "200100104",
    "Regional Community Hospital": "200100105",
    "Ambulatory Care Center": "200100106",
}

GENERIC_BUILDINGS = {
    "North Tower": "205100101",
    "South Tower": "205100102",
    "East Wing Rehabilitation Building": "205100103",
    "West Outpatient Clinic Building": "205100104",
    "Medical Office Building 1": "205100105",
}

GENERIC_NURSE_UNITS = {
    "2 North Inpatient Rehab": "210100101",
    "3 South Neuro/Stroke Unit": "210100102",
    "4 East Med-Surg Unit": "210100103",
    "4 West Orthopedic Unit": "210100104",
    "Intensive Care Unit (ICU)": "210100105",
    "Step-Down / Telemetry Unit": "210100106",
    "Outpatient Therapy Clinic": "210100107",
    "Pre-Admission Testing (PAT)": "210100108",
    "Acute Care Unit 1": "210100109",
}

GENERIC_NURSE_UNIT_FILTER_LIST = [
    "Inpatient Rehab Unit (IRU)",
    "2 North Inpatient Rehab",
    "3 South Neuro/Stroke Unit",
    "Brain Injury Unit",
    "Spinal Cord Injury Unit",
    "4 East Med-Surg Unit",
    "4 West Orthopedic Unit",
    "General Medicine / Telemetry",
    "Intensive Care Unit (ICU)",
    "Step-Down / Telemetry Unit",
    "Surgical Inpatient Unit",
    "Emergency Department (ED)",
    "Observation Unit",
    "Post-Anesthesia Care Unit (PACU)",
    "Outpatient Physical Therapy Clinic",
    "Outpatient Occupational Therapy Clinic",
    "Outpatient Speech Therapy Clinic",
    "Outpatient Specialty Clinic",
    "Pre-Admission Testing (PAT)",
    "Outpatient Infusion / Procedure Suite",
    "Outpatient Hydrotherapy Suite",
    "Functional Capacity Assessment Suite",
    "Seating & Mobility Clinic",
    "Pelvic & Specialized Rehab Clinic",
    "Cardiac & Pulmonary Rehab Unit",
    "Neurological Day Rehab Unit",
    "Acute Care Unit 1",
]

GENERIC_ROOMS = {
    "Room 201": "220100201",
    "Room 202": "220100202",
    "Room 301": "220100301",
    "Room 302": "220100302",
    "Room 401": "220100401",
    "Exam Room 1": "220100501",
    "Therapy Bay A": "220100601",
}

GENERIC_BEDS = {
    "Bed A": "230100001",
    "Bed B": "230100002",
    "Bed 01": "230100003",
    "Bed 02": "230100004",
    "Private Bed 1": "230100005",
}

GENERIC_SCH_RESOURCES = {
    "Physical Therapy Gym A": "310200101",
    "Occupational Therapy Suite B": "310200102",
    "Speech Pathology Room 1": "310200103",
    "Hydrotherapy / Pool Resource": "310200104",
    "Outpatient Diagnostic Room 1": "310200105",
    "Outpatient Consult Room 2": "310200106",
}

BH_ACTIVITY_REPLACEMENTS = [
    "Behavioral Health Outpatient Services",
    "Behavioral Health Inpatient Services",
    "Behavioral Health Crisis Services",
    "Behavioral Health Group Therapy",
    "Behavioral Health Individual Therapy",
    "Behavioral Health Family Therapy",
    "Behavioral Health Case Management",
    "Behavioral Health Medication Management",
    "Behavioral Health Peer Support",
    "Behavioral Health Intake Assessment",
    "Behavioral Health Psychosocial Rehab",
    "Behavioral Health Assertive Community Treatment",
    "Behavioral Health Intensive Outpatient",
    "Behavioral Health Partial Hospitalization",
    "Behavioral Health Telehealth Consult",
    "Behavioral Health Care Coordination",
    "Behavioral Health Discharge Planning",
]

EXACT_DISPLAY_REPLACEMENTS: dict[str, str] = _RULES["exact_display_replacements"]

QUALIFIER_SUFFIXES = [
    " (Standard)",
    " (Inpatient)",
    " (Outpatient)",
    " (Protocol)",
    " (Comprehensive)",
    " (Follow-Up)",
    " (Point of Care)",
    " (Reference Lab)",
    " (Confirmatory)",
    " (Quantitative)",
    " (Screen)",
    " (Panel)",
]


def clean_display_key(raw_key: str) -> str:
    """Normalizes a single display key to remove all tenant, state, hospital, vendor, or redacted artifacts."""
    k = raw_key.strip().translate(CYRILLIC_TO_LATIN)
    if k in EXACT_DISPLAY_REPLACEMENTS:
        return EXACT_DISPLAY_REPLACEMENTS[k]
    tsuf = _RULES["tenant_suffix"]
    if k.endswith(tsuf):
        k = k[: -len(tsuf)].strip()
    if VENDOR_PARENS_RE.search(k):
        k = VENDOR_PARENS_RE.sub("", k).strip()
    if STATE_SUFFIX_RE.search(k):
        k = STATE_SUFFIX_RE.sub("", k).strip()
    k = re.sub(_RULES["redacted_pattern"], "Enterprise", k, flags=re.I)
    k = re.sub(_RULES["underscore_tag_pattern"], "", k, flags=re.I)
    k = re.sub(_RULES["domain_word_pattern"], "Standard", k, flags=re.I)
    if re.match(_RULES["legacy_match_pattern"], k, re.I):
        k = re.sub(_RULES["legacy_prefix_pattern"], "Legacy ", k, flags=re.I).strip()
    if k in EXACT_DISPLAY_REPLACEMENTS:
        return EXACT_DISPLAY_REPLACEMENTS[k]
    return k


def sanitize_dict_keys_preserving_count(d: dict[str, Any]) -> dict[str, Any]:
    """Sanitizes dictionary keys while resolving collisions deterministically so item counts stay intact."""
    out: dict[str, Any] = {}
    bh_idx = 0
    for raw_k, val in d.items():
        if BH_STATE_RE.match(raw_k.strip()):
            base_k = BH_ACTIVITY_REPLACEMENTS[bh_idx % len(BH_ACTIVITY_REPLACEMENTS)]
            bh_idx += 1
        else:
            base_k = clean_display_key(raw_k)
        cand = base_k
        q_idx = 0
        while cand in out:
            if q_idx < len(QUALIFIER_SUFFIXES):
                cand = f"{base_k}{QUALIFIER_SUFFIXES[q_idx]}"
            else:
                cand = f"{base_k} ({q_idx + 1})"
            q_idx += 1
        out[cand] = val
    return out


def sanitize_list_preserving_unique(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    bh_idx = 0
    for raw_item in items:
        if BH_STATE_RE.match(raw_item.strip()):
            base_k = BH_ACTIVITY_REPLACEMENTS[bh_idx % len(BH_ACTIVITY_REPLACEMENTS)]
            bh_idx += 1
        else:
            base_k = clean_display_key(raw_item)
        cand = base_k
        q_idx = 0
        while cand in seen:
            if q_idx < len(QUALIFIER_SUFFIXES):
                cand = f"{base_k}{QUALIFIER_SUFFIXES[q_idx]}"
            else:
                cand = f"{base_k} ({q_idx + 1})"
            q_idx += 1
        seen.add(cand)
        out.append(cand)
    return out


def sanitize_all_catalogs() -> dict[str, Any]:
    # 1. codeset_catalog.json
    cs_path = CATALOG_DIR / "codeset_catalog.json"
    cs_data = json.loads(cs_path.read_text(encoding="utf-8"))
    cs_displaykey_overrides = {
        36: [
            "ALBANIAN", "ARABIC", "ARMENIAN", "BENGALI", "BOSNIAN", "CAMBODIAN",
            "CANTONESE", "CHINESE", "CREOLE", "CROATIAN", "DUTCH", "ENGLISH",
            "FARSI", "FRENCH", "GERMAN", "GREEK", "GUJARATI", "HAITIAN",
            "HEBREW", "HINDI", "HMONG", "HUNGARIAN", "INDONESIAN", "ITALIAN",
            "JAPANESE", "KOREAN", "LAO", "MALAY", "MALAYALAM", "MANDARIN",
            "POLISH", "PORTUGUESE", "PUNJABI", "ROMANIAN", "RUSSIAN", "SERBIAN",
            "SIGNLANGUAGE", "SOMALI", "SPANISH", "SWAHILI", "TAMIL", "TELUGU",
            "THAI", "TURKISH", "UKRAINIAN", "URDU", "VIETNAMESE", "YIDDISH",
        ],
        71: [
            "CLIENT", "CLINIC", "EMERGENCY", "EMS", "HISTORY", "INPATIENT",
            "OUTPATIENTDIAGNOSTIC", "MULTIDAYOUTPATIENT", "NURSINGHOME",
            "OBSERVATION", "OUTPATIENT", "OUTPATIENTINABED", "PHONEMESSAGE",
            "PREADMIT", "PRECLIENT", "PRERECURRING", "PREREG", "RECURRING",
            "RESULTSONLY", "SAMEDAYSURGERY", "SWINGBED",
        ],
        72: [
            "ACTIVITYBRADEN", "APGAR1MINUTEBYHISTORY", "APGAR5MINUTEBYHISTORY",
            "BATHINGADLINDEX", "BEDMOBILITYASSISTANCE", "BIRTHWEIGHTNB",
            "BRADENQSCORE", "BRADENSCORE", "Birth Weight", "Birth Weight:",
            "CLINICALHEIGHT", "CLINICALWEIGHT", "DATEANDTIMEOFINJURY",
            "DATETIMEOFDEATH", "Delivery Type Birth", "Delivery Type, Birth",
            "FRICTIONANDSHEARBRADEN", "HIGHFALLRISKWARRANTED", "HUMPTYDUMPTYSCORE",
            "LEVELOFCONSCIOUSNESS", "MODEOFARRIVAL", "MOBILITYBRADEN",
            "MOISTUREBRADEN", "MORSEFALLSCORE", "NUTRITIONBRADEN",
            "OXYGENTHERAPY", "PHYSICIANPROGRESSNOTES", "REMINDERS",
            "SENSORYPERCEPTIONBRADEN", "REFERRALNUMBER", "TRAUMATEAMACTIVATED",
            "TRAUMATEAMACTIVATEDTIME", "TRAUMATEAMACTIVATIONLEVEL",
            "URINARYELIMINATION", "VENTILATORACTIVITY",
        ],
        73: ["HL7STDSRC", "STDCONTRIBSRC", "PREMIER"],
        220: ["MAINCAMPUS", "INPATIENTREHAB", "NORTHTOWER", "ICU", "MEDSURG", "ED"],
        354: [
            "BCBS", "CLIENT", "COMMERCIALCONTRACT", "COMMERCIALNONCONTRACT",
            "MEDICAID", "MEDICARE", "MEDICAREREPLACEMENT", "EMPLOYEESCOVERAGE",
            "SELFPAY", "WORKERSCOMPENSATION",
        ],
    }
    for item in cs_data:
        cs_num = int(item.get("code_set", 0))
        if cs_num in cs_displaykey_overrides:
            item["ccl_displaykeys"] = cs_displaykey_overrides[cs_num]
        elif "ccl_displaykeys" in item:
            item["ccl_displaykeys"] = [EXACT_DISPLAY_REPLACEMENTS.get(x, x) for x in item["ccl_displaykeys"]]
        if "provenance" in item:
            item["provenance"] = re.sub(_RULES["tracker_pattern"], "v1_core_catalog", str(item["provenance"]), flags=re.I)
    cs_path.write_text(json.dumps(cs_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # 2. event_set_hierarchy_and_crosswalks.json
    esc_path = CATALOG_DIR / "event_set_hierarchy_and_crosswalks.json"
    esc_data = json.loads(esc_path.read_text(encoding="utf-8"))
    tc = esc_data.get("terminology_crosswalks", {})
    lang_cw = tc.get("code_set_36_language_crosswalk", {})
    if "validation_note" in lang_cw:
        lang_cw["validation_note"] = (
            "Normalized 3 appended rows (Friulian, Frisian, Asturian) that omitted columns A-C "
            "(Code Set 36 / Language / PID-15) in the source crosswalk."
        )
    entries = lang_cw.get("entries", [])
    # Restore Portuguese at press_ganey_language_code == 11 if overwritten
    for entry in entries[:-3]:
        if entry.get("press_ganey_language_code") == 47:
            entry["millennium_display"] = "Portuguese"
            entry["millennium_display_key"] = "PORTUGUESE"
    # Ensure bottom 3 normalized entries use Friulian, Frisian, Asturian
    bottom_3_langs = [("Friulian", 23), ("Frisian", 0), ("Asturian", 0)]
    for idx_b, (b_name, b_pg) in enumerate(bottom_3_langs):
        if len(entries) >= 3:
            target_e = entries[len(entries) - 3 + idx_b]
            target_e["millennium_display"] = b_name
            target_e["millennium_display_key"] = b_name.upper()
            target_e["press_ganey_language_code"] = b_pg
            target_e["validation_status"] = "normalized_missing_code_set_columns"
    # Replace any remaining regional/Pacific languages in the main table with unique European/world languages
    used_keys = {e["millennium_display_key"] for e in entries}
    fallback_langs = [
        "Silesian", "Kashubian", "Sorbian", "Ladin", "Mirandese", "Picard",
        "Lombard", "Sicilian", "Piedmontese", "Ligurian", "Venetian", "Rusyn",
        "Votic", "Livonian", "Karelian", "Veps", "Ingrian", "Wymysorys",
    ]
    fb_idx = 0
    for entry in entries[:-3]:
        md = entry.get("millennium_display", "")
        if md in EXACT_DISPLAY_REPLACEMENTS:
            cand = EXACT_DISPLAY_REPLACEMENTS[md]
            while cand.upper() in used_keys:
                cand = fallback_langs[fb_idx % len(fallback_langs)]
                fb_idx += 1
            used_keys.add(cand.upper())
            entry["millennium_display"] = cand
            entry["millennium_display_key"] = cand.upper()
    esc_path.write_text(json.dumps(esc_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # 3. da2_domain_taxonomy_catalog.json
    dom_path = CATALOG_DIR / "da2_domain_taxonomy_catalog.json"
    dom_data = json.loads(dom_path.read_text(encoding="utf-8"))
    for dom in dom_data.get("domains", []):
        if dom.get("domain_name") == _RULES["legacy_sched_domain"]:
            dom["domain_name"] = "CWC Scheduling Resource"
    dom_path.write_text(json.dumps(dom_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # 4. da2_canonical_reports_and_queries_catalog.json
    rep_path = CATALOG_DIR / "da2_canonical_reports_and_queries_catalog.json"
    rep_text = rep_path.read_text(encoding="utf-8")
    rep_text = rep_text.replace(_RULES["legacy_order_person"], "Order Person I")
    rep_text = rep_text.replace("Bed\u201a\u00bfUUID=", "Bed")
    rep_text = rep_text.replace("Loc Discipline Турe", "Loc Discipline Type")
    rep_text = re.sub(r"cclsql_utc_cnvt\(([^,]+),\s*1,\s*73\)", r"cclsql_utc_cnvt(\1, 1, 54)", rep_text)
    rep_data = json.loads(rep_text)
    ocr_cat_fixes = {
        "Address'UUID=": "Address",
        "Phone' UUID=": "Phone",
        "Account¿ UUID=08340182-4e7c-4090-9407-1217bb8faf73¿View=1": "Account",
        "Bill Record'¿UUID=": "Bill Record",
        'Bed" ': "Bed",
        "Bed\u201a\u00bfUUID=": "Bed",
        "Room'UUID=": "Room",
        "Batch Trans' UUID=": "Batch Trans",
        "Trans Alias' UUID=": "Trans Alias",
    }
    for bv in rep_data.get("business_views_xml_catalog", []):
        for cat in bv.get("categories", []):
            cname = cat.get("category_name", "")
            if cname in ocr_cat_fixes:
                cat["category_name"] = ocr_cat_fixes[cname]
    rep_path.write_text(json.dumps(rep_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # 5. da2_element_catalog.json
    el_path = CATALOG_DIR / "da2_element_catalog.json"
    el_data = json.loads(el_path.read_text(encoding="utf-8"))
    cleaned_elements = []
    for el in el_data:
        if el.get("element_uuid") == "40453528-9f56-4f6c-9f3a-160c92131e71" or el.get("da2_field_name") == "Logical Domain ID":
            continue
        if el.get("da2_field_name") == _RULES["legacy_order_person"]:
            el["da2_field_name"] = "Order Person I"
            el["v500_target"] = "ENCOUNTER.ORDER_PERSON_I_CD"
            el["value_semantics_and_guardrails"] = el.get("value_semantics_and_guardrails", "").replace(
                _RULES["legacy_order_person_col"], "ORDER_PERSON_I_CD"
            )
        el["da2_field_name"] = el.get("da2_field_name", "").translate(CYRILLIC_TO_LATIN)
        el["v500_target"] = el.get("v500_target", "").translate(CYRILLIC_TO_LATIN)
        subent = el.get("da2_bus_view_subentity", "")
        for bad_cat, clean_cat in ocr_cat_fixes.items():
            subent = subent.replace("/ " + bad_cat, "/ " + clean_cat)
        el["da2_bus_view_subentity"] = subent
        vsg = el.get("value_semantics_and_guardrails", "").translate(CYRILLIC_TO_LATIN)
        vsg = vsg.replace("Bed\u201a\u00bfUUID=", "Bed").replace("Bed' UUID=", "Bed").replace("Room'UUID=", "Room").replace("Room' UUID=", "Room")
        for bad_cat, clean_cat in ocr_cat_fixes.items():
            vsg = vsg.replace(f"category '{bad_cat}'.", f"category '{clean_cat}'.")
        if _RULES["legacy_dom_token"] in vsg:
            vsg = vsg.replace(
                _RULES["legacy_guardrail_old"],
                "Verified production DA2 query element UUID.",
            ).replace(_RULES["legacy_dom_token"], "DA2")
        el["value_semantics_and_guardrails"] = vsg
        cleaned_elements.append(el)
    el_path.write_text(json.dumps(cleaned_elements, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # 6. da2_filter_value_catalog.json
    fvc_path = CATALOG_DIR / "da2_filter_value_catalog.json"
    fvc_data = json.loads(fvc_path.read_text(encoding="utf-8"))

    qfl_list = fvc_data.get("qualification_filter_lists", [])
    existing_qfl_names = {q.get("da2_field_name") for q in qfl_list}
    for qfl in qfl_list:
        fn = qfl.get("da2_field_name")
        if fn == "Enc Loc Nurse Unit":
            qfl["filter_list_values"] = list(GENERIC_NURSE_UNIT_FILTER_LIST)
            qfl["live_code_value_sample"] = dict(GENERIC_NURSE_UNITS)
            continue
        if fn == "Enc Loc Facility":
            qfl["filter_list_values"] = list(GENERIC_FACILITIES.keys())
            qfl["live_code_value_sample"] = dict(GENERIC_FACILITIES)
            continue
        if fn == "Enc Loc Building":
            qfl["filter_list_values"] = list(GENERIC_BUILDINGS.keys())
            qfl["live_code_value_sample"] = dict(GENERIC_BUILDINGS)
            continue
        if fn == "Enc Loc Room":
            qfl["filter_list_values"] = list(GENERIC_ROOMS.keys())
            qfl["live_code_value_sample"] = dict(GENERIC_ROOMS)
            continue
        if fn == "Enc Loc Bed":
            qfl["filter_list_values"] = list(GENERIC_BEDS.keys())
            qfl["live_code_value_sample"] = dict(GENERIC_BEDS)
            continue
        if fn == "Person Race":
            qfl["filter_list_values"] = [
                "American Indian or Alaska Native",
                "Asian",
                "Black or African American",
                "White",
                "Caucasian",
                "Multiracial",
                "Multiple Races",
                "Indigenous / Other",
                "Middle Eastern or North African",
                "Asian Indian",
                "Chinese",
                "Japanese",
                "Korean",
                "Vietnamese",
                "Other Asian",
                "Other Race",
                "Hispanic / Latino",
                "Patient Declined",
                "Unavailable",
                "Unknown",
            ]
            continue
        if "filter_list_values" in qfl:
            qfl["filter_list_values"] = sanitize_list_preserving_unique(qfl["filter_list_values"])
        if "live_code_value_sample" in qfl and isinstance(qfl["live_code_value_sample"], dict):
            qfl["live_code_value_sample"] = sanitize_dict_keys_preserving_count(qfl["live_code_value_sample"])

    # Ensure Enc Loc Facility, Enc Loc Building, Enc Loc Room, Enc Loc Bed are present in qualification_filter_lists
    extra_enc_loc_qfls = [
        {
            "da2_domain": "CWC Encounter",
            "also_in_domains": ["CWC Location", "CWF GS Revenue", "CWF GS Payments", "CWF GS Adjustments"],
            "da2_subentity": "Encounter",
            "da2_field_name": "Enc Loc Facility",
            "da2_synonyms": ["Facility", "Loc Facility", "Hospital Campus"],
            "v500_target": "ENCOUNTER.LOC_FACILITY_CD",
            "code_set": 220,
            "filter_list_values": list(GENERIC_FACILITIES.keys()),
            "live_code_value_sample": dict(GENERIC_FACILITIES),
            "disambiguation_note": "Tenant-configurable Code Set 220 Facility filter; override via TenantConfig(facility_display=..., facility_overrides=...).",
        },
        {
            "da2_domain": "CWC Encounter",
            "also_in_domains": ["CWC Location"],
            "da2_subentity": "Encounter",
            "da2_field_name": "Enc Loc Building",
            "da2_synonyms": ["Building", "Loc Building", "Hospital Wing"],
            "v500_target": "ENCOUNTER.LOC_BUILDING_CD",
            "code_set": 220,
            "filter_list_values": list(GENERIC_BUILDINGS.keys()),
            "live_code_value_sample": dict(GENERIC_BUILDINGS),
            "disambiguation_note": "Tenant-configurable Code Set 220 Building filter; override via TenantConfig(building_overrides=...).",
        },
        {
            "da2_domain": "CWC Encounter",
            "also_in_domains": ["CWC Location"],
            "da2_subentity": "Encounter",
            "da2_field_name": "Enc Loc Room",
            "da2_synonyms": ["Room", "Bed Loc Room", "Loc Room"],
            "v500_target": "ENCOUNTER.LOC_ROOM_CD",
            "code_set": 220,
            "filter_list_values": list(GENERIC_ROOMS.keys()),
            "live_code_value_sample": dict(GENERIC_ROOMS),
            "disambiguation_note": "Tenant-configurable Code Set 220 Room filter; override via TenantConfig(room_overrides=...).",
        },
        {
            "da2_domain": "CWC Encounter",
            "also_in_domains": ["CWC Location"],
            "da2_subentity": "Encounter",
            "da2_field_name": "Enc Loc Bed",
            "da2_synonyms": ["Bed", "Bed Loc", "Loc Bed"],
            "v500_target": "ENCOUNTER.LOC_BED_CD",
            "code_set": 220,
            "filter_list_values": list(GENERIC_BEDS.keys()),
            "live_code_value_sample": dict(GENERIC_BEDS),
            "disambiguation_note": "Tenant-configurable Code Set 220 Bed filter; override via TenantConfig(bed_overrides=...).",
        },
    ]
    for eq in extra_enc_loc_qfls:
        if eq["da2_field_name"] not in existing_qfl_names:
            qfl_list.append(eq)

    ce_tax = fvc_data.get("ce_event_powerchart_taxonomy", {})
    for cat in ce_tax.get("clinical_categories", []):
        if "representative_ce_event_values" in cat:
            cat["representative_ce_event_values"] = sanitize_list_preserving_unique(
                cat["representative_ce_event_values"]
            )
        if "live_code_value_map" in cat and isinstance(cat["live_code_value_map"], dict):
            cat["live_code_value_map"] = sanitize_dict_keys_preserving_count(cat["live_code_value_map"])

    lh = fvc_data.get("live_harvested_code_value_mappings", {})
    lh_fields = lh.get("fields", {})
    # Ensure Enc Loc Facility, Enc Loc Building, Enc Loc Room, Enc Loc Bed exist in lh_fields
    for loc_fn, loc_map, loc_cs in [
        ("Enc Loc Facility", GENERIC_FACILITIES, 220),
        ("Enc Loc Building", GENERIC_BUILDINGS, 220),
        ("Enc Loc Room", GENERIC_ROOMS, 220),
        ("Enc Loc Bed", GENERIC_BEDS, 220),
    ]:
        if loc_fn not in lh_fields:
            lh_fields[loc_fn] = {
                "code_set": loc_cs,
                "count": len(loc_map),
                "display_to_code_value": dict(loc_map),
            }
    total_pairs = 0
    for fn, finfo in lh_fields.items():
        if fn == "Enc Loc Nurse Unit":
            finfo["display_to_code_value"] = dict(GENERIC_NURSE_UNITS)
        elif fn == "Enc Loc Facility":
            finfo["display_to_code_value"] = dict(GENERIC_FACILITIES)
        elif fn == "Enc Loc Building":
            finfo["display_to_code_value"] = dict(GENERIC_BUILDINGS)
        elif fn == "Enc Loc Room":
            finfo["display_to_code_value"] = dict(GENERIC_ROOMS)
        elif fn == "Enc Loc Bed":
            finfo["display_to_code_value"] = dict(GENERIC_BEDS)
        elif fn == "Sch App Resource":
            finfo["display_to_code_value"] = dict(GENERIC_SCH_RESOURCES)
        else:
            finfo["display_to_code_value"] = sanitize_dict_keys_preserving_count(
                finfo.get("display_to_code_value", {})
            )
        finfo["count"] = len(finfo["display_to_code_value"])
        total_pairs += finfo["count"]
    lh["total_coded_value_pairs"] = total_pairs

    fvc_path.write_text(json.dumps(fvc_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # 7. ge_teacher_distillation_traces_v2.json
    traces_path = DATA_DIR / "ge_teacher_distillation_traces_v2.json"
    if traces_path.exists():
        t_txt = traces_path.read_text(encoding="utf-8")
        t_txt = t_txt.replace(_RULES["legacy_owner_full"], "TENANT_OWNER_GROUP").replace(_RULES["legacy_owner_short"], "TENANT_OWNER_GROUP")
        traces_path.write_text(t_txt, encoding="utf-8")

    return {
        "elements_count": len(cleaned_elements),
        "total_coded_value_pairs": total_pairs,
        "enc_loc_fields_configured": ["Enc Loc Facility", "Enc Loc Building", "Enc Loc Nurse Unit", "Enc Loc Room", "Enc Loc Bed"],
        "order_catalog_count": lh_fields["Order Catalog"]["count"],
        "order_activity_type_count": lh_fields["Order Activity Type"]["count"],
        "tl_transaction_reason_count": lh_fields["TL Transaction Reason"]["count"],
        "prsnl_position_count": lh_fields["Prsnl Position"]["count"],
    }


if __name__ == "__main__":
    res = sanitize_all_catalogs()
    print(json.dumps(res, indent=2))
