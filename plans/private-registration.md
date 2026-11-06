# Private publisher sign-in

Status: source 3b3a7de passed the full image build, 107 focused registration tests and all existing runtime/privacy checks. Independent review found no serious issues. Eighteen isolated real-React browser cases pass across three widths. Full Next layout and live staff-login acceptance remain open. Not deployed.

The publisher serves the existing team behind Social Studio. Turning off registration must prevent every new public account, including an empty installation or a generic identity provider. Existing linked staff accounts keep their current sign-in method. This does not introduce a staff-domain allowlist or link accounts by matching email.

## Changes

1. Make `DISABLE_REGISTRATION=true` refuse all new registrations. Bootstrap requires an administrator's explicit configuration change; there is no automatic public exception.
2. Keep existing provider-account lookup before that creation gate. Keep password, activation and OAuth state checks.
3. Refuse an unknown provider callback with HTTP403 and fixed `registration_disabled` code. Do not return its provider token or show the company/signup form.
4. Render a clear private-publisher message and a login link. Other callback failures use fixed retry wording. Hide signup links when the server says registration is closed.

## Acceptance

Use synthetic accounts and stub provider/database/mail/tracking calls. Closed registration must deny local, Google and generic new accounts with zero or existing organizations; final register/login paths must remain guarded. Existing local and Google accounts must sign in without creating another account. Open mode retains the supported registration flow. Invalid state/identity stays blocked. Frontend failure must neither expose a token nor remain on an endless spinner. Review the actual UI at phone, tablet and desktop widths before calling the journey verified.

No database migration, account deletion, permission grant, provider call or configuration change is included in local acceptance. Main controls image publication/deployment separately after the capacity hold is resolved. Live staff access remains an explicit release acceptance check.
