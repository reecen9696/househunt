# Passed-In Property Tracker — Chrome extension

Adds a toolbar button that saves the property listing you're currently
viewing (realestate.com.au, domain.com.au, property.com.au) into your
passed-in-finder tracker — either a local `passedin serve` or the hosted
deployment.

## Install (unpacked)

1. Open `chrome://extensions`
2. Enable **Developer mode** (top right)
3. **Load unpacked** → select this `chrome-extension/` folder
4. Pin "Passed-In Property Tracker" to the toolbar
5. Open its **options** page and set the server:
   - hosted: `https://passedin-reece.fly.dev` plus the username and password
   - local: `http://127.0.0.1:8765`, password left blank

The address and password live in `chrome.storage.sync`, so they follow your
Chrome profile rather than being baked into the extension. The Authorization
header is only sent when a password is set, so a local server with no auth
sees exactly the request it always did.

## Use

1. Open any property listing page
2. Click the extension icon — badge shows **✓** saved / **✗** failed
   (wrong address or password on the options page, or a local server that
   isn't running) / **n/a** (not a listing site)
3. See it in the **Property tracker** tab on the server

What gets captured (all optional except the URL, editable in the UI later):
address, suburb, price guide, beds/baths/cars, property type, agency,
listing date (when the page exposes it — drives "time on market"), photo.
