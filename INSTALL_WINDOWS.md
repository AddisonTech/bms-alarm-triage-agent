# Installing this project on a fresh Windows PC

Written for someone starting from a machine with nothing on it: no
Python, no Git, no Ollama, no copy of this repository. Follow it top to
bottom and do not skip a step because it looks obvious.

Every command is PowerShell. Open PowerShell by pressing the Windows key,
typing `powershell`, and pressing Enter. You do not need to run it as
Administrator for any step here.

Budget about 30 minutes, most of which is downloads.

## What you need before you start

- Windows 10 or 11, 64-bit
- About 15 GB of free disk space (5 GB of that is the language model)
- An internet connection
- An NVIDIA GPU is **optional**. Everything runs without one, just slower.

---

## Step 1 - Install Python

```powershell
winget install --id Python.Python.3.12 --exact --source winget
```

**Close PowerShell and open a new window.** The installer changes your
PATH and the window you ran it in will not see the change.

Check it worked:

```powershell
python --version
```

You should see `Python 3.12.10` or another 3.12.x. If you see nothing,
or a "not recognized" error, see Troubleshooting at the bottom.

## Step 2 - Install Git

```powershell
winget install --id Git.Git --exact --source winget
```

**Close PowerShell and open a new window again.** Then check:

```powershell
git --version
```

You should see `git version 2.x.x.windows.x`.

## Step 3 - Get the code

This puts the project in your user folder. Change the path if you want it
somewhere else, but remember where you put it.

```powershell
cd $HOME
git clone https://github.com/AddisonTech/bms-alarm-triage-agent.git
cd bms-alarm-triage-agent
```

You are now in the project folder. **Every remaining command in this
document assumes you are in this folder.** If you close PowerShell and
come back later, get back here with:

```powershell
cd $HOME\bms-alarm-triage-agent
```

## Step 4 - Create the virtual environment

A virtual environment keeps this project's Python packages separate from
everything else on the machine.

```powershell
python -m venv .venv
```

This creates a `.venv` folder inside the project. It takes a few seconds
and prints nothing when it works.

## Step 5 - Read this before you try to "activate" anything

Most Python instructions on the internet tell you to run
`.venv\Scripts\activate` next. **On a default Windows machine that
command fails**, with an error like:

```
.venv\Scripts\Activate.ps1 cannot be loaded because running scripts
is disabled on this system.
```

This is not a broken install. Windows ships with PowerShell script
execution disabled, and the activate script is a PowerShell script.

**You do not need to fix this.** This project never requires activation.
Instead of activating and then typing `python`, you call the virtual
environment's Python directly by its full path:

```powershell
.venv\Scripts\python.exe
```

That is the entire workaround. Wherever another document says `python`,
you type `.venv\Scripts\python.exe` instead. Every command in the rest of
this file already does this, so you can just copy them.

**If you would rather turn activation on anyway**, this one command
permits locally-created scripts for your user account only, and does not
require Administrator:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

Answer `Y` when it asks. After that `.venv\Scripts\activate` works. This
is optional and nothing below depends on it.

## Step 6 - Install the project

```powershell
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

The second command reads `pyproject.toml`, pulls down LangGraph and
pytest and their dependencies, and installs this project in editable
mode. It prints a lot and takes a minute or two. The last line should say
`Successfully installed` followed by a list of packages.

## Step 7 - Prove the code works, before adding a model

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Expect **340 passed** in about 15 seconds. These tests use a recorded
model response and never contact a language model, so they must pass at
this point even though you have not installed Ollama yet.

If they pass, the agent is correctly installed. Everything after this
point is about the one step that uses a language model.

---

## Step 8 - Install Ollama

Ollama is the program that runs the language model locally on this
machine. Nothing is sent over the internet at run time.

```powershell
winget install --id Ollama.Ollama --exact --source winget
```

**Close PowerShell and open a new window**, then get back to the project
folder:

```powershell
cd $HOME\bms-alarm-triage-agent
ollama --version
```

You should see `ollama version 0.33.x` or later.

The installer sets Ollama to start automatically with Windows and sit in
the system tray. If you do not want that, delete this file:

```powershell
Remove-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Ollama.lnk"
```

Then you start and stop it by hand -- see Step 10.

## Step 9 - Download the language model

```powershell
ollama pull llama3.1:8b-instruct-q4_K_M
```

This downloads **4.9 GB** and takes several minutes. It lands in
`C:\Users\<you>\.ollama\models`.

That model name is not arbitrary. It is the one written in
`config\triage.json`, and the project reads the name from that file. If
you pull a different model, you must also edit `config\triage.json` to
match, or the project will ask Ollama for a model it does not have.

## Step 10 - Starting and stopping the model server

If you deleted the startup shortcut in Step 8, Ollama is not running and
you start it yourself before a demo:

```powershell
Start-Process ollama -ArgumentList 'serve' -WindowStyle Hidden
```

Confirm it is up (this should print `200`):

```powershell
(Invoke-WebRequest http://127.0.0.1:11434/api/tags -UseBasicParsing).StatusCode
```

Stop it when you are done:

```powershell
Get-Process ollama*, llama-server -ErrorAction SilentlyContinue | Stop-Process -Force
```

**`llama-server` in that stop command is not a typo and not optional.**
Leaving it out orphans the process that holds the model in graphics
memory, and the next run is roughly 60 percent slower.

`DEMO.md` covers the rest: warming the model before a recording, the
expected run times, and what to do if the server is not up. Read it
before you record anything.

---

## Step 11 - The one command that proves the install worked

```powershell
.venv\Scripts\python.exe -m tools.demo --split holdout
```

This runs the whole pipeline over the labeled holdout data and scores
itself. It takes about 3 seconds if Ollama is not running, or about 46
seconds if it is.

**The last twelve lines should look exactly like this:**

```
  escalation recall ......... 100.0%  (15 of 15 labeled faults escalated)
  volume reduction .......... 30.4x  (548 alarm events -> 18 items for review)
  false escalation rate ..... 0.0%  (0 of 15 escalations unsupported)

  the three criteria from P5-C1:
    1. top-5 capture       15 of 12 required                    PASS
    2. volume reduction    30.4x, order of magnitude required   PASS
    3. false escalation    0.0%, ceiling 20%                    PASS

========================================================================
overall: PASS        1.84 s in process, excluding interpreter startup
========================================================================
```

The time on the last line will differ. **Every other number must match.**
If they do, the install is correct and complete.

Near the top of the output, one line tells you whether the language model
was used:

- `N8 model: local model` - Ollama is running and wrote the text live.
- `N8 model: recorded response` - Ollama is not running, so a saved
  response was replayed. **The numbers above are identical either way**,
  because the model only writes the explanatory sentences and takes no
  part in the scoring.

---

## Troubleshooting

| What went wrong | What to do |
|---|---|
| `python : The term 'python' is not recognized` | You did not open a new PowerShell window after installing Python. Close it and open a new one. |
| `winget : The term 'winget' is not recognized` | Your Windows is too old or App Installer is missing. Install "App Installer" from the Microsoft Store, then retry. |
| `Activate.ps1 cannot be loaded because running scripts is disabled` | Expected. Do not activate. Use `.venv\Scripts\python.exe` instead, as in Step 5. |
| `pip install` fails with a compiler or "Microsoft Visual C++" error | Rare here, since no dependency needs compiling. Check you are on Python 3.12 and not 3.13 or newer. |
| `pip install` fails with `OSError: [Errno 2] No such file or directory` naming a very long path under `.venv\Lib\site-packages` | The project folder is nested too deep. Windows refuses paths over 260 characters and one dependency has long filenames. Move the project nearer the drive root -- `C:\Users\<you>\bms-alarm-triage-agent` as in Step 3 is short enough -- delete `.venv`, and redo Steps 4 and 6. |
| Tests fail at Step 7 | Something is wrong with the install, not the project. Delete the `.venv` folder and redo Steps 4, 6, and 7. |
| `ollama : The term 'ollama' is not recognized` | You did not open a new PowerShell window after installing Ollama. |
| Demo says `recorded response` but you want live text | Ollama is not running. Start it with the Step 10 command, then run the demo again. |
| Demo numbers do not match Step 11 | Stop. Do not proceed. Run `.venv\Scripts\python.exe -m pytest -q` -- if the corpus has been modified, the freeze test fails and names the file. |
| Everything is very slow with the model running | Check for orphaned processes with `nvidia-smi`, then use the full stop command in Step 10 and start again. |

## Where things are when you are done

| What | Where |
|---|---|
| The project | `C:\Users\<you>\bms-alarm-triage-agent` |
| Python packages for it | `...\bms-alarm-triage-agent\.venv` |
| The language model | `C:\Users\<you>\.ollama\models` |
| Ollama itself | `C:\Users\<you>\AppData\Local\Programs\Ollama` |
| Thresholds and the model name | `...\bms-alarm-triage-agent\config\triage.json` |
| How to run the demo | `DEMO.md` in the project folder |
