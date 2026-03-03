from bs4 import BeautifulSoup

from scraper.domains import AmazonHandler


def test_amazon_handler_parses_price_from_a_offscreen_and_meta_currency() -> None:
    html = """
    <html>
      <head>
        <meta property="og:price:currency" content="USD" />
      </head>
      <body>
        <input id="ASIN" value="B0DT6ZBDYS" />
        <span id="productTitle">MagSafe Car Mount</span>
        <span class="a-offscreen">$78.99</span>
      </body>
    </html>
    """

    handler = AmazonHandler("https://www.amazon.com/dp/B0DT6ZBDYS")
    handler.request_data = BeautifulSoup(html, "html.parser")

    assert handler._get_product_name() == "MagSafe Car Mount"
    assert handler._get_product_price() == 78.99
    assert handler._get_product_currency() == "USD"
    assert handler._get_product_id() == "B0DT6ZBDYS"


def test_amazon_handler_falls_back_to_meta_price_and_url_asin() -> None:
    html = """
    <html>
      <head>
        <title>Some Product Title</title>
        <meta property="og:price:amount" content="129.5" />
      </head>
      <body></body>
    </html>
    """

    handler = AmazonHandler("https://www.amazon.com/some-product/dp/B012345678")
    handler.request_data = BeautifulSoup(html, "html.parser")

    assert handler._get_product_name() == "Some Product Title"
    assert handler._get_product_price() == 129.5
    assert handler._get_product_id() == "B012345678"
