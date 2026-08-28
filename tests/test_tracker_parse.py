"""Listing-page parsing for tracked properties (server-side add-by-URL)."""
import json

from passedin.tracker import parse_listing

_LISTING_HTML = """
<html><head>
<script type="application/ld+json">%s</script>
</head><body>
<script>
window.data = {"listing": {
  "address": {"suburb": "Windsor", "postcode": "3181",
              "display": {"fullAddress": "12 Peel Street, Windsor, Vic 3181"}},
  "priceText": "$1,100,000 - $1,200,000",
  "propertyType": {"display": "House"},
  "bedrooms": 3, "bathrooms": 2, "carSpaces": 1,
  "landSize": {"displayValue": "359", "sizeUnit": {"displayValue": "m\\u00b2"}},
  "dateFirstListed": "2026-07-20T00:00:00",
  "inspections": [{"display": {"longLabel": "Inspection Sat 15 Aug 10:30 am"},
                   "startTime": "2026-08-15T10:30:00+10:00"}],
  "auction": {"display": {"longLabel": "Auction Sat 15 Aug 11:00 am"}},
  "listingCompany": {"name": "Ray White - Prahran",
                     "branding": {"primaryColor": "#ffe512"}},
  "listers": [{"id": "1", "name": "Raphael Calik"}],
  "mainImage": {"templatedUrl": "https://i2.au.reastatic.net/{size}/aa/main.jpg"},
  "floorplans": [{"templatedUrl": "https://i2.au.reastatic.net/{size}/bb/plan.jpg"}]
}};
</script>
</body></html>
""" % json.dumps({
    "@type": "Residence",
    "address": {"streetAddress": "12 Peel Street", "addressLocality": "Windsor",
                "postalCode": "3181"},
})


def test_parse_listing_full():
    d = parse_listing(_LISTING_HTML,
                      "https://www.realestate.com.au/property-house-vic-windsor-1?cid=x")
    assert d["url"].endswith("windsor-1")
    assert d["address"] == "12 Peel Street, Windsor, Vic 3181"
    assert d["suburb"] == "Windsor"
    assert d["postcode"] == "3181"
    assert d["price_text"] == "$1,100,000 - $1,200,000"
    assert (d["price_low"], d["price_high"]) == (1100000, 1200000)
    assert d["property_type"] == "House"
    assert (d["bedrooms"], d["bathrooms"], d["car_spaces"]) == (3, 2, 1)
    assert d["land_size_sqm"] == 359.0
    assert d["date_listed"] == "2026-07-20"
    assert d["inspection_text"] == "Inspection Sat 15 Aug 10:30 am"
    assert d["auction_text"] == "Auction Sat 15 Aug 11:00 am"
    assert d["agency_name"] == "Ray White - Prahran"
    assert d["agent_name"] == "Raphael Calik"
    assert d["agency_color"] == "#ffe512"
    assert d["image_url"] == "https://i2.au.reastatic.net/800x600/aa/main.jpg"
    assert d["floorplan_url"] == "https://i2.au.reastatic.net/1000x750/bb/plan.jpg"


def test_parse_listing_sparse_page_still_returns_url():
    d = parse_listing("<html><body><h1>hello</h1></body></html>",
                      "https://www.realestate.com.au/property-2")
    assert d["url"] == "https://www.realestate.com.au/property-2"
    assert "bedrooms" not in d


def test_related_listings_do_not_leak_into_the_main_listing():
    """Listing pages embed a 'recommended properties' carousel. Its fields
    must never fill in for the main listing — a field the main listing
    genuinely lacks (land:null here) must stay absent, not inherit 1,680 m²
    from a neighbouring card. Regression: observed live on a Richmond listing.
    """
    main = (
        '{"listing":{"address":{"suburb":"Richmond","postcode":"3121",'
        '"display":{"fullAddress":"21 Baker Street, Richmond, Vic 3121"}},'
        '"priceText":"PRIVATE SALE: $980,000 - $1,078,000",'
        '"propertyType":{"id":"house","display":"House"},'
        '"listingCompany":{"name":"Real Agency",'
        '"branding":{"primaryColour":"#000000","textColour":"#ffffff"}},'
        '"generalFeatures":{"bedrooms":{"value":3},"bathrooms":{"value":1},'
        '"parkingSpaces":{"value":0}},'
        '"propertySizes":{"building":null,"land":null,"preferred":null}}}'
    )
    # Padding puts the related block beyond the anchor window, as on the real
    # page (~9KB away), rather than adjacent to the main listing.
    related = (
        '{"fullAddress":"912 High Street, Reservoir, Vic 3073",'
        '"generalFeatures":{"bedrooms":{"value":6},"bathrooms":{"value":3},'
        '"parkingSpaces":{"value":4}},'
        '"propertySizes":{"building":null,"land":{"displayValue":"1,680",'
        '"sizeUnit":{"displayValue":"m²"}}},'
        '"propertyType":{"display":"House"}}'
    )
    html = f"<html><body><script>{main}{'/*pad*/' * 1200}{related}</script></body></html>"

    d = parse_listing(html, "https://www.realestate.com.au/property-house-vic-richmond-151506112")
    assert d["address"] == "21 Baker Street, Richmond, Vic 3121"
    assert (d["bedrooms"], d["bathrooms"], d["car_spaces"]) == (3, 1, 0)
    assert d["agency_color"] == "#000000"      # British spelling handled
    assert d["property_type"] == "House"       # id before display handled
    assert "land_size_sqm" not in d            # NOT the related listing's 1,680


def test_agency_address_does_not_masquerade_as_the_listing():
    """A listing page also carries the *agency's* own address, in the same
    suburb and often appearing first. Matching on suburb alone picked the
    agency's office (Gary Peer's 348 Orrong Road) instead of the property.
    The page title names the listing and nothing else, so it decides.
    """
    html = (
        '<title>8 Salisbury Street, Caulfield North, Vic 3161 - House for Sale'
        ' - realestate.com.au</title>'
        '<script>{"agency":{"address":{"display":'
        '{"fullAddress":"348 Orrong Road, CAULFIELD NORTH, VIC, 3161"}},'
        '"generalFeatures":{"bedrooms":{"value":9},"bathrooms":{"value":9},'
        '"parkingSpaces":{"value":9}}},'
        '"listing":{"address":{"display":'
        '{"fullAddress":"8 Salisbury Street, Caulfield North, Vic 3161"}},'
        '"generalFeatures":{"bedrooms":{"value":2},"bathrooms":{"value":1},'
        '"parkingSpaces":{"value":0}}}}</script>'
    )
    d = parse_listing(html, "https://www.realestate.com.au/property-house-vic-caulfield+north-152018548")
    assert d["address"] == "8 Salisbury Street, Caulfield North, Vic 3161"
    assert (d["bedrooms"], d["bathrooms"], d["car_spaces"]) == (2, 1, 0)


def test_title_without_a_street_number_is_ignored():
    # A generic title must not be mistaken for an address.
    html = ('<title>Real Estate &amp; Property for Sale - realestate.com.au</title>'
            '<script>{"address":{"display":{"fullAddress":"9 Real Street, Windsor, Vic 3181"}}}</script>')
    d = parse_listing(html, "https://www.realestate.com.au/property-house-vic-windsor-1")
    assert d["address"] == "9 Real Street, Windsor, Vic 3181"


def test_og_image_wins_over_a_nearer_json_mainimage():
    """og:image names THIS listing's hero shot. The JSON mainImage keys
    repeat per media block, and the one nearest the address anchor belongs
    to a different block — picking it showed the wrong photo on the card.
    """
    html = (
        '<meta property="og:image" content="https://cdn/hero/image.jpg">'
        '<script>{"mainImage":{"templatedUrl":"https://cdn/{size}/wrong.jpg"},'
        '"address":{"display":{"fullAddress":"128 Montague Street, South Melbourne, Vic 3205"}}}'
        '</script>'
    )
    d = parse_listing(html, "https://www.realestate.com.au/property-house-vic-south+melbourne-150109264")
    assert d["image_url"] == "https://cdn/hero/image.jpg"


def test_parse_listing_jsonld_address_fallback():
    html = """<script type="application/ld+json">
    {"@type":"Event","address":{"streetAddress":"5 High St","addressLocality":"Prahran","postalCode":"3181"}}
    </script>"""
    d = parse_listing(html, "https://x.example/p")
    assert d["address"] == "5 High St"
    assert d["suburb"] == "Prahran"


# --- how the property is being sold ------------------------------------------
# The card has to say what you're working to: an auction date, or the absence
# of one. REA writes "auction":null on everything that isn't going under the
# hammer, so a null is a positive statement of private sale rather than a
# gap in the data.

_AUCTION_HTML = """
<html><head><title>11A Murdoch Street, Camberwell, Vic 3124 - House for Sale</title>
</head><body><script>
window.data = {"listing": {
  "address": {"display": {"fullAddress": "11A Murdoch Street, Camberwell, Vic 3124"}},
  "auction": {"dateTime": {"value": "2026-09-12T11:00:00+10:00",
                           "display": {"longLabel": "Sat 12 Sep at 11:00 am"}}},
  "listers": [{"name": "Campbell Ward", "_links": {"canonical":
      "https://www.realestate.com.au/agent/campbell-ward-1936738?cid={cid}"}}]
}};
</script></body></html>
"""

_PRIVATE_HTML = """
<html><head><title>335 Bambra Road, Caulfield South, Vic 3162 - House for Sale</title>
</head><body><script>
window.data = {"listing": {
  "address": {"display": {"fullAddress": "335 Bambra Road, Caulfield South, Vic 3162"}},
  "auction": null,
  "listers": [{"name": "Dion Besser", "_links": {"canonical":
      "https://www.realestate.com.au/agent/dion-besser-328641?cid={cid}"}}]
}};
</script></body></html>
"""

_URL = "https://www.realestate.com.au/property-house-vic-camberwell-152065760"
_PRIVATE_URL = ("https://www.realestate.com.au/"
                "property-house-vic-caulfield+south-151882992")


def test_scheduled_auction_yields_a_sortable_date():
    d = parse_listing(_AUCTION_HTML, _URL)
    assert d["sale_method"] == "auction"
    assert d["auction_date"] == "2026-09-12"
    assert d["auction_text"] == "Sat 12 Sep at 11:00 am"
    # The raw timestamp is consumed into auction_date, not left to confuse
    # the storage layer with a column that doesn't exist.
    assert "auction_datetime" not in d


def test_null_auction_reads_as_private_sale():
    d = parse_listing(_PRIVATE_HTML, _PRIVATE_URL)
    assert d["sale_method"] == "private"
    assert d.get("auction_date") is None


def test_unknown_sale_method_stays_unknown():
    """No auction key at all is missing data, not a private sale — saying
    "Private sale" on the card would be asserting something unverified."""
    d = parse_listing("<html><title>1 X Street, Windsor, Vic 3181 - House"
                      "</title><body></body></html>",
                      "https://www.realestate.com.au/property-house-vic-windsor-9")
    assert d.get("sale_method") is None


def test_agent_profile_url_is_captured_for_dating():
    """The agent page is where REA publishes a listed date, so the link has
    to survive onto the tracked row."""
    d = parse_listing(_AUCTION_HTML, _URL)
    assert d["agent_profile_url"] == \
        "https://www.realestate.com.au/agent/campbell-ward-1936738"


def test_auction_is_found_far_from_the_address_anchor():
    """Regression: the auction block sits 7.1-7.7KB from the address in the
    payload, depending on how much media a listing carries — straddling the
    anchor window. Anchoring it kept the date on short pages and silently
    dropped it on long ones, so four of eleven real listings came back with
    no sale method at all. `"auction":` occurs at most once per page (checked
    across 27 cached listings), so it is read page-wide instead.
    """
    filler = '"related":[' + ','.join(
        '{"fullAddress":"%d Other Street, Camberwell, Vic 3124"}' % i
        for i in range(200)) + '],'
    html = ('<html><head><title>11A Murdoch Street, Camberwell, Vic 3124 - '
            'House for Sale</title></head><body><script>window.data = '
            '{"listing": {"address": {"display": {"fullAddress": '
            '"11A Murdoch Street, Camberwell, Vic 3124"}},'
            + filler +
            '"auction": {"dateTime": {"value": "2026-09-19T12:30:00+10:00",'
            '"display": {"longLabel": "Sat 19 Sep at 12:30 pm"}}}}};'
            '</script></body></html>')
    # The filler has to actually push the auction block out of the window,
    # or this test would pass without exercising anything.
    assert html.index('"auction"') - html.index('"fullAddress"') > 7500
    d = parse_listing(html, _URL)
    assert d["auction_date"] == "2026-09-19"
    assert d["sale_method"] == "auction"
