import re
import requests
from bs4 import BeautifulSoup
import json
import logging
from abc import ABC, abstractmethod

from scraper.models import Info
from scraper.constants import REQUEST_HEADER, REQUEST_COOKIES
from scraper.filemanager import Config
from scraper.exceptions import WebsiteVersionNotSupported


def request_url(url: str) -> requests.Response:
    request_timeout = Config.get_request_timeout()

    try:
        response = requests.get(url, headers=REQUEST_HEADER, cookies=REQUEST_COOKIES, timeout=request_timeout)
        return response
    except requests.RequestException:
        logging.getLogger(__name__).exception(f"Module requests exception with url: {url}")


class BaseWebsiteHandler(ABC):
    def __init__(self, url: str) -> None:
        self.url = url
        self.website_name = get_website_name(url)
        self.info: Info = None
        self.request_data = None

    def get_product_info(self) -> Info:
        try:
            self._request_product_data()
            self._get_common_data()
            raw_name = self._get_product_name()
            name = Config.get_user_product_name(raw_name)
            price = self._get_product_price()
            currency = self._get_product_currency()
            id = self._get_product_id()
            self.info = Info(name, price, currency, id)
            return self.info
        except (AttributeError, ValueError, TypeError):
            logging.getLogger(__name__).exception(f"Could not get all the data needed from url: {self.url}")
            return Info(None, None, None, None, valid=False)
        except WebsiteVersionNotSupported as ex:
            logging.getLogger(__name__).error(ex)
            return Info(None, None, None, None, valid=False)

    def _request_product_data(self) -> None:
        # option for each specific class to change how the request data is being handled
        response = request_url(self.url)
        self.request_data = BeautifulSoup(response.text, "html.parser")

    def _get_common_data(self) -> None:
        # if the same data needs to be accessed from more than one of the abstract methods,
        # then you can use this method to store the data as a instance variable,
        # so that the other methods can access the data
        pass

    @abstractmethod
    def _get_product_name(self) -> str:
        pass

    @abstractmethod
    def _get_product_price(self) -> float:
        pass

    @abstractmethod
    def _get_product_currency(self) -> str:
        pass

    @abstractmethod
    def _get_product_id(self) -> str:
        pass

    @abstractmethod
    def get_short_url(self) -> str:
        pass


class KomplettHandler(BaseWebsiteHandler):
    def _get_product_name(self) -> str:
        return self.request_data.find("div", class_="product-main-info__info").h1.span.text

    def _get_product_price(self) -> float:
        return float(self.request_data.find("span", class_="product-price-now").text.strip(",-").replace(".", ""))

    def _get_product_currency(self) -> str:
        script_tag = self.request_data.find("script", type="application/ld+json").contents[0]
        currency = json.loads(script_tag).get("offers").get("priceCurrency")
        return currency

    def _get_product_id(self) -> str:
        return self.url.split("/")[4]

    def get_short_url(self) -> str:
        id = self._get_product_id()
        website = get_website_name(self.url, keep_tld=True, keep_http=True, keep_www=True)
        return f"{website}/product/{id}"


class ProshopHandler(BaseWebsiteHandler):
    def _get_common_data(self) -> None:
        self.script_json = {}

        script_tags = self.request_data.find_all("script", type="application/ld+json")

        for script_tag in script_tags:
            contents = script_tag.string or "".join(script_tag.contents)
            if not contents:
                continue

            try:
                script_json = json.loads(contents)
            except json.JSONDecodeError:
                continue

            script_candidates = []

            if isinstance(script_json, dict):
                script_candidates.append(script_json)

                graph = script_json.get("@graph")
                if isinstance(graph, list):
                    script_candidates.extend(elem for elem in graph if isinstance(elem, dict))

            if isinstance(script_json, list):
                script_candidates.extend(elem for elem in script_json if isinstance(elem, dict))

            for candidate in script_candidates:
                # Use data that looks like a product schema.
                if any(("name" in candidate, "offers" in candidate)):
                    self.script_json = candidate
                    break

            if self.script_json:
                break

        # Fallback values when product schema JSON isn't present.
        if not self.script_json:
            title_tag = self.request_data.find("meta", property="og:title")
            if title_tag and title_tag.get("content"):
                self.script_json["name"] = title_tag.get("content")

            currency_tag = self.request_data.find("meta", property="product:price:currency")
            if currency_tag and currency_tag.get("content"):
                self.script_json["offers"] = {"priceCurrency": currency_tag.get("content")}

    def _get_product_name(self) -> str:
        if self.script_json.get("name"):
            return self.script_json["name"]

        og_title_tag = self.request_data.find("meta", property="og:title")
        if og_title_tag and og_title_tag.get("content"):
            return og_title_tag.get("content")

        title_tag = self.request_data.find("title")
        if title_tag and title_tag.text:
            return title_tag.text.strip()

        product_title_tag = self.request_data.find("h1")
        if product_title_tag and product_title_tag.text:
            return product_title_tag.text.strip()

        # Final fallback: infer a readable name from URL slug.
        slug = self.url.rstrip("/").split("/")[-2] if len(self.url.rstrip("/").split("/")) > 1 else self.url
        return slug.replace("-", " ")

    def _get_product_price(self) -> float:
        price_selectors = [
            ("span", {"class": "site-currency-attention"}),
            ("div", {"class": "site-currency-attention site-currency-campaign"}),
            ("div", {"class": "site-currency-attention"}),
            ("meta", {"itemprop": "price"}),
        ]

        for tag_name, tag_attrs in price_selectors:
            tag = self.request_data.find(tag_name, tag_attrs)

            if not tag:
                continue

            raw_price = tag.get("content") if tag_name == "meta" else tag.text

            if not raw_price:
                continue

            try:
                return parse_price_string(raw_price)
            except ValueError:
                continue

        script_price = self.script_json.get("offers", {}).get("price")
        if script_price is not None:
            try:
                return parse_price_string(str(script_price))
            except ValueError:
                pass

        price_meta_tag = self.request_data.find("meta", property="product:price:amount")

        if price_meta_tag and price_meta_tag.get("content"):
            return parse_price_string(price_meta_tag.get("content"))

        raise AttributeError("Could not find product price for Proshop page")

    def _get_product_currency(self) -> str:
        currency = self.script_json.get("offers", {}).get("priceCurrency")

        if currency:
            return currency

        currency_tag = self.request_data.find("meta", property="product:price:currency")
        if currency_tag and currency_tag.get("content"):
            return currency_tag.get("content")

        return "N/F"

    def _get_product_id(self) -> str:
        return self.url.split("/")[-1]

    def get_short_url(self) -> str:
        id = self._get_product_id()
        website = get_website_name(self.url, keep_tld=True, keep_http=True, keep_www=True)
        return f"{website}/{id}"


class ComputerSalgHandler(BaseWebsiteHandler):
    def _get_product_name(self) -> str:
        return self.request_data.find("meta", {"name": "title"})["content"]

    def _get_product_price(self) -> float:
        return float(self.request_data.find("span", itemprop="price").text.strip().replace(".", "").replace(",", "."))

    def _get_product_currency(self) -> str:
        return self.request_data.find("span", itemprop="priceCurrency").get("content")

    def _get_product_id(self) -> str:
        return self.url.split("/")[4]

    def get_short_url(self) -> str:
        id = self._get_product_id()
        website = get_website_name(self.url, keep_tld=True, keep_http=True, keep_www=True)
        return f"{website}/i/{id}"


class ElgigantenHandler(BaseWebsiteHandler):
    def _get_common_data(self) -> None:
        self.elgiganten_api_data = self._get_json_api_data()

    def _get_product_name(self) -> str:
        return self.request_data.find("h1", class_="product-title").text

    def _get_product_price(self) -> float:
        return float(self.elgiganten_api_data["data"]["product"]["currentPricing"]["price"]["value"])

    def _get_product_currency(self) -> str:
        return self.elgiganten_api_data["data"]["product"]["currentPricing"]["price"]["currency"]

    def _get_product_id(self) -> str:
        return self.url.split("/")[-1]

    def _get_json_api_data(self) -> dict:
        id_number = self._get_product_id()

        # API link to get price and currency
        if "elgiganten.dk" in self.url:
            api_link = f"https://www.elgiganten.dk/cxorchestrator/dk/api?getProductWithDynamicDetails&appMode=b2c&user=anonymous&operationName=getProductWithDynamicDetails&variables=%7B%22articleNumber%22%3A%22{id_number}%22%2C%22withCustomerSpecificPrices%22%3Afalse%7D&extensions=%7B%22persistedQuery%22%3A%7B%22version%22%3A1%2C%22sha256Hash%22%3A%22229bbb14ee6f93449967eb326f5bfb87619a37e7ee6c4555b94496313c139ee1%22%7D%7D"  # noqa E501
        elif "elgiganten.se" in self.url:
            api_link = f"https://www.elgiganten.se/cxorchestrator/se/api?getProductWithDynamicDetails&appMode=b2c&user=anonymous&operationName=getProductWithDynamicDetails&variables=%7B%22articleNumber%22%3A%22{id_number}%22%2C%22withCustomerSpecificPrices%22%3Afalse%7D&extensions=%7B%22persistedQuery%22%3A%7B%22version%22%3A1%2C%22sha256Hash%22%3A%22229bbb14ee6f93449967eb326f5bfb87619a37e7ee6c4555b94496313c139ee1%22%7D%7D"  # noqa E501
        else:
            raise WebsiteVersionNotSupported(get_website_name(self.url, keep_tld=True))

        response = request_url(api_link)
        return response.json()

    def get_short_url(self) -> str:
        id = self._get_product_id()
        website = get_website_name(self.url, keep_tld=True, keep_http=True, keep_www=True)
        return f"{website}/product/{id}"


class AvXpertenHandler(BaseWebsiteHandler):
    def _get_common_data(self) -> None:
        soup_script_tag = self.request_data.find("script", type="application/ld+json").contents[0]
        self.script_json = json.loads(soup_script_tag)

    def _get_product_name(self) -> str:
        return self.request_data.find("div", class_="content-head").h1.text.strip()

    def _get_product_price(self) -> float:
        return float(self.request_data.find("div", class_="price").text.replace("\xa0DKK", "").replace(" DKK", ""))

    def _get_product_currency(self) -> str:
        return self.script_json.get("offers").get("priceCurrency")

    def _get_product_id(self) -> str:
        return self.script_json.get("sku")

    def get_short_url(self) -> str:
        return self.url


class AvCablesHandler(BaseWebsiteHandler):
    def _get_product_name(self) -> str:
        return self.request_data.find("h1", class_="title").text

    def _get_product_price(self) -> float:
        return float(
            self.request_data.find("div", class_="regular-price")
            .text.strip()
            .replace("Pris:   ", "")
            .replace("Tilbudspris:   ", "")
            .split(",")[0]
        )

    def _get_product_currency(self) -> str:
        return self.request_data.find("meta", property="og:price:currency").get("content")

    def _get_product_id(self) -> str:
        script_tag = self.request_data.find("script", type="application/ld+json").contents[0]
        id = json.loads(script_tag).get("sku")
        return str(id)

    def get_short_url(self) -> str:
        return self.url


class AmazonHandler(BaseWebsiteHandler):
    def _get_product_name(self) -> str:
        product_title = self.request_data.find("span", id="productTitle")
        if product_title and product_title.text:
            return product_title.text.strip()

        og_title_tag = self.request_data.find("meta", property="og:title")
        if og_title_tag and og_title_tag.get("content"):
            return og_title_tag.get("content")

        title_tag = self.request_data.find("title")
        if title_tag and title_tag.text:
            return title_tag.text.strip()

        asin = self._get_product_id()
        return f"Amazon product {asin}"

    def _get_product_price(self) -> float:
        price_selectors = [
            ("span", {"class": "a-price aok-align-center"}),
            ("span", {"class": "a-price"}),
            ("span", {"class": "a-offscreen"}),
            ("span", {"id": "priceblock_ourprice"}),
            ("span", {"id": "priceblock_dealprice"}),
            ("meta", {"property": "og:price:amount"}),
        ]

        for tag_name, attrs in price_selectors:
            tag = self.request_data.find(tag_name, attrs)

            if not tag:
                continue

            raw_price = tag.get("content") if tag_name == "meta" else tag.text

            if tag_name == "span" and hasattr(tag, "span") and tag.span and tag.span.text:
                raw_price = tag.span.text

            if not raw_price:
                continue

            number_string = get_number_string(raw_price.replace(" ", ""))
            if not number_string:
                continue

            try:
                return parse_price_string(number_string)
            except ValueError:
                continue

        raise AttributeError("Could not find product price for Amazon page")

    def _get_product_currency(self) -> str:
        currency_meta_tag = self.request_data.find("meta", property="og:price:currency")
        if currency_meta_tag and currency_meta_tag.get("content"):
            return currency_meta_tag.get("content")

        regex_pattern = "%22currencyCode%22%3A%22(.{3})%22"

        regex_result = re.search(regex_pattern, str(self.request_data))

        if regex_result:
            return regex_result.group(1)

        if "$" in str(self.request_data):
            return "USD"

        return "N/F"

    def _get_product_id(self) -> str:
        try:
            return self.request_data.find("input", id="ASIN").get("value")
        except (AttributeError, ValueError, TypeError):
            cr_state_object_tag = self.request_data.find("span", id="cr-state-object")

            if cr_state_object_tag and cr_state_object_tag.get("data-state"):
                asin_json = json.loads(cr_state_object_tag.get("data-state"))
                return asin_json["asin"]

            asin_match = re.search(r"/dp/([A-Z0-9]{10})", self.url)

            if asin_match:
                return asin_match.group(1)

            raise AttributeError("Could not find ASIN for Amazon page")

    def get_short_url(self) -> str:
        return self.url


class EbayHandler(BaseWebsiteHandler):
    def _get_common_data(self) -> None:
        self.soup_url = self.request_data.find("meta", property="og:url").get("content")

    def _get_product_name(self) -> str:
        try:
            return self.request_data.find("h1", class_="x-item-title__mainTitle").text.strip()
        except (AttributeError, ValueError, TypeError):
            return self.request_data.find("meta", property="og:title").get("content").replace("  | eBay", "")

    def _get_product_price(self) -> float:
        if self.soup_url.split("/")[3] == "itm":
            price = float(self.request_data.find("div", class_="x-price-primary").text.replace("US $", ""))
        else:
            price = float(
                self.request_data.find("div", class_="x-price-primary")
                .text.replace("DKK ", "")
                .replace("$", "")
                .replace(",", "")
            )

        return price

    def _get_product_currency(self) -> str:
        if self.soup_url.split("/")[3] == "itm":
            currency = self.request_data.find("span", itemprop="priceCurrency").get("content")
        else:
            script_tag = self.request_data.find("script", type="application/ld+json").contents[0]
            currency = (
                json.loads(script_tag)
                .get("mainEntity")
                .get("offers")
                .get("itemOffered")[0]
                .get("offers")[0]
                .get("priceCurrency")
            )

        return currency

    def _get_product_id(self) -> str:
        return self.url.split("/")[4].split("?")[0]

    def get_short_url(self) -> str:
        id = self._get_product_id()
        website = get_website_name(self.url, keep_tld=True, keep_http=True, keep_www=True)

        if self.url.split("/")[3] == "itm":
            return f"{website}/itm/{id}"
        else:
            return f"{website}/p/{id}"


class PowerHandler(BaseWebsiteHandler):
    def _get_common_data(self) -> None:
        id = self._get_product_id()
        self.api_json = request_url(f"https://www.power.dk/api/v2/products?ids={id}").json()

    def _get_product_name(self) -> str:
        return self.api_json[0].get("title")

    def _get_product_price(self) -> float:
        return float(self.api_json[0].get("price"))

    def _get_product_currency(self) -> str:
        return "DKK"

    def _get_product_id(self) -> str:
        return self.url.split("/")[-2].strip("p-")

    def get_short_url(self) -> str:
        id = self._get_product_id()
        url_id = self.url.split("/")[3]
        website = get_website_name(self.url, keep_tld=True, keep_http=True, keep_www=True)
        return f"{website}/{url_id}/p-{id}"


class ExpertHandler(BaseWebsiteHandler):
    def _get_common_data(self) -> None:
        id = self._get_product_id()
        self.api_json = request_url(f"https://www.expert.dk/api/v2/products?ids={id}").json()

    def _get_product_name(self) -> str:
        return self.api_json[0].get("title")

    def _get_product_price(self) -> float:
        return float(self.api_json[0].get("price"))

    def _get_product_currency(self) -> str:
        return "DKK"

    def _get_product_id(self) -> str:
        return self.url.split("/")[-2].strip("p-")

    def get_short_url(self) -> str:
        id = self._get_product_id()
        url_id = self.url.split("/")[3]
        website = get_website_name(self.url, keep_tld=True, keep_http=True, keep_www=True)
        return f"{website}/{url_id}/p-{id}"


class MMVisionHandler(BaseWebsiteHandler):
    def _get_common_data(self) -> None:
        soup_script_tag = self.request_data.find_all("script", type="application/ld+json")[1].contents[0]
        self.script_json = json.loads(soup_script_tag)

    def _get_product_name(self) -> str:
        return self.request_data.find("h1", itemprop="name").text.strip()

    def _get_product_price(self) -> float:
        return float(
            self.request_data.find("h3", class_="product-price text-right")
            .text.strip("fra ")
            .strip()
            .strip(",-")
            .replace(".", "")
        )

    def _get_product_currency(self) -> str:
        return self.script_json.get("offers").get("priceCurrency")

    def _get_product_id(self) -> str:
        return self.script_json.get("productID")

    def get_short_url(self) -> str:
        return self.url


class CoolshopHandler(BaseWebsiteHandler):
    def _get_product_name(self) -> str:
        return self.request_data.find("div", class_="thing-header").h1.text.strip().replace("\n", " ")

    def _get_product_price(self) -> float:
        return float(self.request_data.find("meta", property="product:price:amount")["content"].split(".")[0])

    def _get_product_currency(self) -> str:
        return self.request_data.find("meta", property="product:price:currency").get("content")

    def _get_product_id(self) -> str:
        return self.request_data.find_all("div", id="attributeSku")[1].text.strip()

    def get_short_url(self) -> str:
        url_id = self.url.split("/")[-2]
        website = get_website_name(self.url, keep_tld=True, keep_http=True, keep_www=True)
        return f"{website}/produkt/{url_id}/"


class SharkGamingHandler(BaseWebsiteHandler):
    def _get_product_name(self) -> str:
        return self.request_data.find("h1", class_="page-title").span.text

    def _get_product_price(self) -> float:
        return float(self.request_data.find("meta", property="product:price:amount").get("content"))

    def _get_product_currency(self) -> str:
        return self.request_data.find("meta", property="product:price:currency").get("content")

    def _get_product_id(self) -> str:
        return json.loads(self.request_data.find_all("script", type="application/ld+json")[3].text).get("productID")

    def get_short_url(self) -> str:
        return self.url


class NeweggHandler(BaseWebsiteHandler):
    def _get_common_data(self) -> None:
        script_data_raw = self.request_data.find_all("script", type="application/ld+json")[2].text
        self.script_json = json.loads(script_data_raw)

    def _get_product_name(self) -> str:
        return self.script_json.get("name")

    def _get_product_price(self) -> float:
        return float(self.script_json.get("offers").get("price"))

    def _get_product_currency(self) -> str:
        return self.script_json.get("offers").get("priceCurrency")

    def _get_product_id(self) -> str:
        return self.url.split("/")[5].split("?")[0]

    def get_short_url(self) -> str:
        id = self._get_product_id()
        website = get_website_name(self.url, keep_tld=True, keep_http=True, keep_www=True)
        return f"{website}/p/{id}"


class HifiKlubbenHandler(BaseWebsiteHandler):
    def _get_common_data(self) -> None:
        script_data_raw = self.request_data.findAll("script", type="application/ld+json")[1].text
        self.product_data = json.loads(script_data_raw)["offers"]

    def _get_product_name(self) -> str:
        brand_name = self.request_data.find("span", class_="product-page__brand-name").text
        model_name = self.request_data.find("span", class_="product-page__model-name").text
        return f"{brand_name} {model_name}"

    def _get_product_price(self) -> float:
        return float(self.product_data.get("price"))

    def _get_product_currency(self) -> str:
        return self.product_data.get("priceCurrency")

    def _get_product_id(self) -> str:
        return self.url.split("/")[4]

    def get_short_url(self) -> str:
        id = self._get_product_id()
        website = get_website_name(self.url, keep_tld=True, keep_http=True, keep_www=True)
        return f"{website}/{id}"


class SheinHandler(BaseWebsiteHandler):
    def _get_common_data(self) -> None:
        script_data_raw = self.request_data.find_all("script", type="application/ld+json")[1].text
        self.script_json = json.loads(script_data_raw)

    def _get_product_name(self) -> str:
        return self.script_json.get("name")

    def _get_product_price(self) -> float:
        return float(self.script_json.get("offers").get("price"))

    def _get_product_currency(self) -> str:
        return self.script_json.get("offers").get("priceCurrency")

    def _get_product_id(self) -> str:
        return self.script_json.get("sku")

    def get_short_url(self) -> str:
        return self.url


def get_website_name(url: str, keep_tld=False, keep_http=False, keep_www=False, keep_subdomain=True) -> str:
    stripped_url = url if keep_http else url.removeprefix("https://").removeprefix("http://")

    if not keep_www and keep_http:
        stripped_url = stripped_url.replace("www.", "", 1)
    elif not keep_www:
        stripped_url = stripped_url.removeprefix("www.")

    domain = "/".join(stripped_url.split("/")[0:3]) if keep_http else stripped_url.split("/")[0]

    # Remove the TLD/DNS name (such as ".com") if keep_tld is false
    website_name_list = domain.split(".") if keep_tld else domain.split(".")[:-1]

    # Remove subdomain if keep_subdomain is false
    if not keep_subdomain and len(website_name_list) > 1:
        subdomain_and_domain = get_website_name(domain, keep_subdomain=True)
        subdomains = subdomain_and_domain.split(".")[:-1]

        website_name_list_copy = website_name_list.copy()
        # remove subdomains
        website_name_list = [elem for elem in website_name_list_copy if elem not in subdomains]

    website_name = ".".join(website_name_list)
    return website_name


def get_website_handler(url: str) -> BaseWebsiteHandler:
    website_name = get_website_name(url, keep_subdomain=False).lower()

    website_handler = SUPPORTED_DOMAINS.get(website_name, None)

    if not website_handler:
        logging.getLogger(__name__).error(f"Can't find a website handler - website: '{website_name}' possibly not supported")
        return None

    return website_handler(url)


def get_number_string(value: str) -> str:
    """Return string with only digits, commas (,) and periods (.)"""
    text_pattern = re.compile(r"[^\d.,]+")
    result = text_pattern.sub("", value)
    return result


def parse_price_string(value: str) -> float:
    """Parse localized price text to float.

    Handles common thousands/decimal separator variants such as:
    - 3.999,95
    - 3,999.95
    - 3999.95
    - 3999,95
    - 3 999,95
    """
    number_string = get_number_string(value)

    if not number_string:
        raise ValueError("Price string contains no digits")

    has_comma = "," in number_string
    has_dot = "." in number_string

    if has_comma and has_dot:
        # Treat the right-most separator as decimal separator.
        if number_string.rfind(",") > number_string.rfind("."):
            normalized = number_string.replace(".", "").replace(",", ".")
        else:
            normalized = number_string.replace(",", "")
    elif has_comma:
        whole, fractional = number_string.rsplit(",", 1)
        normalized = f"{whole.replace(',', '')}.{fractional}" if len(fractional) <= 2 else number_string.replace(",", "")
    elif has_dot:
        whole, fractional = number_string.rsplit(".", 1)
        normalized = f"{whole.replace('.', '')}.{fractional}" if len(fractional) <= 2 else number_string.replace(".", "")
    else:
        normalized = number_string

    return float(normalized)


SUPPORTED_DOMAINS: dict[str, BaseWebsiteHandler] = {
    "komplett": KomplettHandler,
    "proshop": ProshopHandler,
    "computersalg": ComputerSalgHandler,
    "elgiganten": ElgigantenHandler,
    "avxperten": AvXpertenHandler,
    "av-cables": AvCablesHandler,
    "amazon": AmazonHandler,
    "ebay": EbayHandler,
    "power": PowerHandler,
    "expert": ExpertHandler,
    "mm-vision": MMVisionHandler,
    "coolshop": CoolshopHandler,
    "sharkgaming": SharkGamingHandler,
    "newegg": NeweggHandler,
    "hifiklubben": HifiKlubbenHandler,
    "shein": SheinHandler,
}
