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


def test_proshop_handler_name_falls_back_to_title_when_og_title_missing() -> None:
    html = """
    <html>
      <head>
        <title>Roborock Qrevo S White - Proshop</title>
      </head>
      <body></body>
    </html>
    """

    handler = ProshopHandler("https://www.proshop.dk/Robotstoevsuger/Roborock-Robotstoevsuger-Qrevo-S-White/3353131")
    handler.request_data = BeautifulSoup(html, "html.parser")

    handler._get_common_data()

    assert handler._get_product_name() == "Roborock Qrevo S White - Proshop"


def test_proshop_handler_parses_json_ld_from_graph_list() -> None:
    html = """
    <html>
      <head>
        <script type="application/ld+json">
            {"@context":"https://schema.org","@graph":[{"@type":"BreadcrumbList"},{"@type":"Product","name":"Roborock Qrevo S White","offers":{"priceCurrency":"DKK"}}]}
        </script>
      </head>
      <body></body>
    </html>
    """

    handler = ProshopHandler("https://www.proshop.dk/Robotstoevsuger/Roborock-Robotstoevsuger-Qrevo-S-White/3353131")
    handler.request_data = BeautifulSoup(html, "html.parser")

    handler._get_common_data()

    assert handler._get_product_name() == "Roborock Qrevo S White"
    assert handler._get_product_currency() == "DKK"


def test_proshop_handler_price_falls_back_to_json_ld_offer_price() -> None:
    html = """
    <html>
      <head>
        <script type="application/ld+json">
            {"@type":"Product","name":"Roborock Qrevo S White","offers":{"price":"3999.50","priceCurrency":"DKK"}}
        </script>
      </head>
      <body></body>
    </html>
    """

    handler = ProshopHandler("https://www.proshop.dk/Robotstoevsuger/Roborock-Robotstoevsuger-Qrevo-S-White/3353131")
    handler.request_data = BeautifulSoup(html, "html.parser")

    handler._get_common_data()

    assert handler._get_product_price() == 3999.5
