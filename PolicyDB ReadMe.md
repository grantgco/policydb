# PolicyDB ReadMe



## Installation and Packaging

  PolicyDB.zip — 20 MB, ready to email. It contains:                                                                    

  - launch.command — the double-click launcher

  - wheels/ — 38 pre-downloaded wheels (Apple Silicon, no internet required on work Mac)

  - README.txt — setup instructions

    **To Send:**
  1. Email PolicyDB.zip (it's in your project root)                                                                     

  2. Recipient unzips it somewhere permanent (Desktop or Documents — not Downloads)                                     

  3. Double-click launch.command → right-click → Open the first time (Gatekeeper)                                       

  4. Terminal installs everything (~30 sec), browser opens automatically                                                

  5. Every time after: just double-click launch.command             

     **To Rebuild:**

To rebuild the package after future updates:                                                                          

```bash
./scripts/make_package.sh            
```

Runs in ~30 seconds, overwrites PolicyDB.zip in the project root.                                                                                                  