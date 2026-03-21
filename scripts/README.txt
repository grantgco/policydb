PolicyDB — Quick Start
======================

Requirements
------------
  - macOS (Apple Silicon or Intel)
  - Python 3.11 or newer

  Don't have Python? Download it from:
    https://www.python.org/downloads/
  Install it, then come back here.


Install (do this once)
----------------------
  1. Unzip this folder somewhere permanent
     (e.g. your Desktop or Documents — NOT your Downloads folder)

  2. Open Terminal  (Spotlight: Cmd+Space, type "Terminal", hit Enter)

  3. Drag the PolicyDB folder into the Terminal window.
     Terminal will type the path for you. Now edit the line so it reads:

       bash /path/to/PolicyDB/install.sh

     (Just type "bash " at the start, then drag the folder in, then
      add "/install.sh" at the end.)

     Or, if you prefer, cd into the folder and run:

       cd ~/Desktop/PolicyDB
       bash install.sh

  4. The installer will set everything up (~30 seconds) and open
     your browser automatically when it's ready.


Every Day After That
--------------------
  PolicyDB auto-starts when you log in — no action needed.

  To open the browser:
    - Type "policydb" in any Terminal window

  To stop the server:
    - Type "policydb stop" in Terminal

  To start it again manually:
    - Type "policydb" in Terminal


Upgrading
---------
  Unzip the new package over the old one and run:

    bash install.sh

  Your data is preserved — only the app is updated.


Uninstalling
------------
  Open Terminal and run:

    bash /path/to/PolicyDB/install.sh uninstall

  This removes the app but keeps your data at ~/.policydb/.
  Delete that folder too if you want a clean removal.


Your Data
---------
  Database:  ~/.policydb/policydb.sqlite
  Config:    ~/.policydb/config.yaml
  Backups:   ~/.policydb/backups/
  Exports:   ~/.policydb/exports/
  Log:       ~/.policydb/server.log

  Your data stays on your computer — nothing is sent anywhere.


Troubleshooting
---------------
  "Python not found" error
    Install Python from https://www.python.org/downloads/
    After installing, run "bash install.sh" again.

  Server didn't auto-start after login
    Open Terminal and just type: policydb

  Browser doesn't open automatically
    Open a browser and go to: http://127.0.0.1:8000

  Want to start fresh
    Delete the folder: ~/.policydb/venv
    Run "bash install.sh" again to reinstall.
