# Market Flow 0.18.0 + Smart Money 0.6.1 hotfix

Smart Money 0.6.0 could stop immediately with:

`ModuleNotFoundError: No module named 'requests'`

The Executive/OGE collector uses the requests library, but it was missing from
the Smart Money Docker requirements. Version 0.6.1 installs it explicitly.

No database reset or configuration change is required. Rebuild/reinstall Smart
Money Tracker after replacing the add-on folder. Market Flow remains 0.18.0.
