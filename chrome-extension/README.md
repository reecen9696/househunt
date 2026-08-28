# Passed-In Property Tracker — Chrome extension

Adds a toolbar button that saves the property listing you're currently
viewing (realestate.com.au, domain.com.au, property.com.au) into the local
passed-in-finder tracker.

## Install (unpacked)

1. Open `chrome://extensions`
2. Enable **Developer mode** (top right)
3. **Load unpacked** → select this `chrome-extension/` folder
4. Pin "Passed-In Property Tracker" to the toolbar

## Use

1. Have the tracker server running: `python -m passedin serve`
2. Open any property listing page
3. Click the extension icon — badge shows **✓** saved / **✗** failed
   (server not running is the usual cause) / **n/a** (not a listing site)
4. See it in the **Property tracker** tab at http://127.0.0.1:8765/

What gets captured (all optional except the URL, editable in the UI later):
address, suburb, price guide, beds/baths/cars, property type, agency,
listing date (when the page exposes it — drives "time on market"), photo.
