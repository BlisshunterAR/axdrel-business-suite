# WhatsApp Appointment Spider

Makes one report of all your WhatsApp client chats for a day (or any date range):
for every chat it shows the **number**, the **name** (saved name, or the name the client gave),
**what they ordered**, their **address**, and a **short summary** of what was asked and planned.

## How to use

1. Double-click **Start WhatsApp Spider.bat**. The first time, it installs Python if it is missing.
2. On your phone, open each chat you want, tap **⋮ → More → Export chat → Without media**,
   and save or send the file to the PC (e.g. WhatsApp to yourself, email, Google Drive, OneDrive).
   iPhone: open the chat, tap the name → **Export chat → Without media**.
3. Put the exported files (.txt or .zip) in the **Exported chats** folder (button "Open" in the program).
   You can drop them in as-is; re-exporting the same chat later just replaces the old one.
4. Choose **Today**, **Yesterday**, or a **From/To** range and click **Make report now**.
   The report opens in your browser. An Excel-friendly .csv is saved next to it.

## Daily schedule

Set a time (e.g. 20:00) and click **Turn on**. Every day at that time the program makes the report
for the last N days into the Reports folder, without opening a window. Tip: set the Reports folder to
OneDrive/Google Drive or a shared network folder to read the report on your other PC.

## Phone numbers for saved contacts

WhatsApp's export only shows the *name* for saved contacts, not the number. To always show the number,
put your contacts file in the Exported chats folder once: a **.vcf** (phone Contacts → Export) or a
**.csv** with "contact" in the file name (e.g. Google Contacts export).

## Smarter summaries (optional)

Without a key, the program finds orders, addresses and names with simple rules (Dutch and English words).
With a Claude API key (console.anthropic.com) it writes proper summaries in any language.
Note: with a key, the chat text of the chosen days is sent to Anthropic's API to make the summary.
