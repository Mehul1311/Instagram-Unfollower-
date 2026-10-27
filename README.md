# AccUnfollower

A local Instagram following-cleanup tool for people who do not want to waste time unfollowing accounts one by one. Add the usernames you have already decided to unfollow, preview the list, and process it in controlled, resumable batches from a browser dashboard or the command line.

> [!IMPORTANT]
> AccUnfollower uses Instagram's unofficial private API through `instagrapi`. Automation may violate Instagram's terms or trigger challenges, temporary restrictions, or account suspension. Use it only with your own account, keep batches small, and stop immediately if Instagram displays a warning or verification request.

## Why use it?

- Save time when cleaning up a long following list.
- Preview every target before making account changes.
- Run small batches with randomized delays between actions.
- Pause after a batch, stop immediately, and resume later.
- Skip accounts already completed in an earlier run.
- Use a private dashboard that is served only on your computer.
- Import simple JSON lists and common Instagram data-export shapes.

AccUnfollower does **not** discover non-followers or decide whom you should unfollow. You provide the target list and remain in control of every run.

## Requirements

- Python 3.11 or newer
- An Instagram account that you own
- Windows PowerShell for the commands below (the Python scripts also work on macOS and Linux with the equivalent activation commands)

## Installation

```powershell
git clone <your-repository-url>
cd AccUnfollower
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
Copy-Item people.example.json people.json
```

Open `.env` and enter the credentials for your own account:

```dotenv
INSTAGRAM_USERNAME=your_username
INSTAGRAM_PASSWORD=your_password
```

Your `.env`, `people.json`, saved session, and run history are ignored by Git. Never commit or share them: the session file and credentials can provide access to your account, while the target and history files contain private account data.

## Add your unfollow list

Edit `people.json` using the included example as a starting point:

```json
{
  "usernames": [
    "person_one",
    "@person.two",
    "https://www.instagram.com/person_three/",
    { "username": "person_four" }
  ]
}
```

Plain arrays, usernames, `@handles`, Instagram profile URLs, and the common Instagram `relationships_following` / `string_list_data` export format are supported. Duplicate and invalid values are ignored.

## Use the dashboard

Start the local server:

```powershell
python dashboard_app.py
```

Then open [http://127.0.0.1:8765](http://127.0.0.1:8765) in your browser.

From the dashboard you can upload a JSON target list, choose the batch size and delay range, run one batch, or queue all remaining batches with a cooldown. **Pause after batch** completes the current batch before pausing; **Stop now** terminates the active batch. Recorded progress remains available when you restart.

The server binds to `127.0.0.1` by default, so it is not exposed to other devices on your network. Keep the terminal process running while the dashboard is in use.

## Use the command line

Preview the first batch without logging in or changing your account:

```powershell
python instagram_unfollow.py people.json
```

Process up to 10 targets after typing the confirmation phrase:

```powershell
python instagram_unfollow.py people.json --execute
```

Use a smaller batch and longer randomized delay:

```powershell
python instagram_unfollow.py people.json --execute --limit 5 --delay-min 45 --delay-max 90
```

Useful options:

| Option | Purpose |
| --- | --- |
| `--limit N` | Process at most `N` remaining targets; `0` means all targets. |
| `--delay-min N` | Set the minimum delay between actions in seconds. |
| `--delay-max N` | Set the maximum delay between actions in seconds. |
| `--yes` | Skip the confirmation prompt when `--execute` is used. |
| `--retry-completed` | Include targets already marked complete in the history. |
| `--session-file PATH` | Use a different private Instagram session file. |
| `--history-file PATH` | Use a different progress-history file. |

Running every target at once is not recommended for a large list.

## How resuming works

Completed actions are appended to `.unfollow_history.jsonl`. Future runs skip usernames marked as successfully unfollowed or already not followed, so interrupted work can continue without starting over.

The first successful login creates `.instagram_session.json`. Reusing this private session reduces repeated password logins. If Instagram requests a challenge or manual verification, stop the tool, complete the check in the official Instagram app or website, and wait before trying again. Do not loop-retry errors.

## Run the tests

```powershell
python -m unittest discover -s tests -v
```

## Project structure

```text
AccUnfollower/
|-- dashboard/               # Local dashboard HTML, CSS, and JavaScript
|-- tests/                   # Parsing, history, and dashboard tests
|-- dashboard_app.py         # Flask dashboard server and batch runner
|-- instagram_unfollow.py    # Preview and unfollow CLI
|-- people.example.json      # Safe example target list
|-- .env.example             # Credential template
`-- requirements.txt         # Python dependencies
```

## Privacy and safety

- Credentials are loaded locally from `.env`; the dashboard does not send them to the browser.
- The dashboard, session, history, and target list remain local unless you deliberately share them.
- The tool removes your own username from the target list as a safety check.
- Instagram warnings and throttling errors stop processing instead of repeatedly retrying.
- Review your target list and use preview mode before every real run.

This project is not affiliated with, endorsed by, or sponsored by Instagram or Meta. You are responsible for how you use it and for complying with the platform's terms.
