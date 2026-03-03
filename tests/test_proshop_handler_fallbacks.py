from bs4 import BeautifulSoup

from scraper.domains import ProshopHandler


def test_proshop_handler_parses_meta_fallbacks_when_ld_json_missing() -> None:
    html = """
    <html>
      <head>
        <meta property="og:title" content="Roborock Qrevo S White" />
        <meta property="product:price:amount" content="3999.95" />
        <meta property="product:price:currency" content="DKK" />
      </head>
      <body></body>
    </html>
    """

    handler = ProshopHandler("https://www.proshop.dk/Robotstoevsuger/Roborock-Robotstoevsuger-Qrevo-S-White/3353131")
    handler.request_data = BeautifulSoup(html, "html.parser")

    handler._get_common_data()

    assert handler._get_product_name() == "Roborock Qrevo S White"
    assert handler._get_product_price() == 3999.95
    assert handler._get_product_currency() == "DKK"
