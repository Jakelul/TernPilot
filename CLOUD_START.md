# TernPilot cloud development

Canonical source: https://github.com/Jakelul/TernPilot
Install packages: https://github.com/Jakelul/TernPilot/releases

## ChatGPT cloud Work

Continue in the existing `TernPilot 클라우드 개발` task. In its execution
environment, clone the public repository and run the offline tests:

```sh
git clone https://github.com/Jakelul/TernPilot.git
cd TernPilot
python3 -m unittest test_router.py
```

If a checkout already exists, inspect `git status` before updating. Preserve
local changes and earlier snapshots. Cloud changes are not automatically
pushed to GitHub or installed on a Mac. Publish reviewed changes through an
authorized GitHub integration.

## GitHub Codespaces

The repository includes a minimal Python devcontainer. Use GitHub's
**Code → Codespaces** menu to create a cloud environment. Its setup runs
offline tests without model calls. Codespaces is subject to the user's
GitHub quota and billing; this release does not start a Codespace.

The Linux container is for source development and tests. The installer and
Finder launcher target macOS. Do not enable global Codex instructions in the
cloud just to run tests.

## Another Mac mini

Download and unpack the macOS release ZIP. Prepare Python 3.9+, Git and the
provider CLIs, and authenticate each CLI on that Mac. Run
`Install TernPilot.command`; choose `y` to enable bounded delegation in new
Codex conversations. README.md explains scope and data flow.

Do not transfer CLI login files, API keys, the entire Codex home or unrelated
history. Gemini starts disabled on fresh installs; enable it only after
that account's CLI eligibility has been verified.
