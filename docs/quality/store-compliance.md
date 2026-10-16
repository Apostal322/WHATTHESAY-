# Chrome Web Store compliance checklist

A working checklist, **not a compliance claim**: nothing here has been reviewed by Google or a
lawyer, and no listing exists. Sources to re-read before submission: the
[program policies](https://developer.chrome.com/docs/webstore/program-policies/policies), the
[MV3 requirements](https://developer.chrome.com/docs/webstore/program-policies/mv3-requirements)
and the [user data FAQ](https://developer.chrome.com/docs/webstore/user_data).

| Topic | State in the code | Status | Who |
|---|---|---|---|
| Single purpose | Verify a statement from the YouTube video being watched | Ready to describe | Owner |
| Remote code | All code bundled; `npm run check:remote-code` in CI fails on remote scripts, `eval`, `new Function`, `importScripts`, or a CSP that allows them | Checked automatically | – |
| Permissions | `sidePanel`; host access to `https://www.youtube.com/*` and the backend origin only | Minimal; justify each in the listing | Owner |
| Backend origin | Today `http://127.0.0.1:8000`; a public build needs the HTTPS backend origin instead | Blocked on deployment | DevOps |
| Data sent off the device | Video id, playback position, duration, the claim text (edited by the user) and its extracted context, sent to the developer's backend; the backend sends the public video URL and the claim to Google Gemini (and, in search mode, queries to the search provider) | Must be disclosed in the privacy policy and the Data usage form | Owner |
| Data not collected | Browsing history, other tabs, cookies, account data, page contents beyond video id, title and position | Matches code (`docs/security-privacy.md`) | – |
| Third-party processing | Google Gemini API (and optionally Brave Search); free-tier inputs may be used by Google | Disclosure; consider a paid tier before launch | Owner (legal) |
| Retention | Extension: nothing stored. Backend: in-memory cache (1 h), JSON logs without claim text | Document retention for logs in production | DevOps |
| Secure transmission | HTTP to localhost only; production needs HTTPS | Blocked on deployment | DevOps |
| User consent | Data leaves the device only after the user clicks Verify or submits a claim | Describe in the listing | Owner |
| Privacy policy | Not written | Required before submission | Owner (legal) |
| Accuracy of the listing | Must say the tool is AI-assisted, can be wrong, and shows sources | Draft from README | Owner |
| Affiliation | Must not imply endorsement by YouTube or Google | Wording check | Owner |
| Developer account, listing assets | – | Account action | Owner |
