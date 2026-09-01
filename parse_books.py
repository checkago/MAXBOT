import json
import math
import time
from typing import Any

from playwright.sync_api import sync_playwright


CATALOG_URL = "https://biblio.mosreg.ru/books"
GRAPHQL_URL = "https://api.biblio.mosreg.ru/graphql"
TARGET_DISTRICT = "Балашиха"
OUTPUT_FILE = "books_balashiha.json"
PAGE_LIMIT = 100
MAX_BUCKET = 1000
MAX_PREFIX_DEPTH = 4

CHARSET = list(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
    "0123456789"
    "abcdefghijklmnopqrstuvwxyz"
    "\"'«»()[]{}+-.,:;!?@#$%^&*_=/\\"
)

URBAN_DISTRICTS_QUERY = """
query SiteUrbanDistricts {
  UrbanDistricts { title: shortUrbanDistrictName id: urbanDistrictId name: urbanDistrictName }
}
"""

HOUSES_QUERY = """
query HousesOfCultureSelectList($limit: Int, $page: Int, $listUrbanDistrictId: [String]) {
  HousesOfCulture(limit: $limit, page: $page, listUrbanDistrictId: $listUrbanDistrictId) {
    data { houseOfCultureId name }
    has_more_pages
  }
}
"""

BOOK_CATEGORIES_QUERY = """
query CommonBooksCategories {
  BookCategory { data { id: bookCategoryId name } }
}
"""

BOOK_CATALOG_QUERY = """
query BookCatalog($houseOfCultureId: [String], $search: BookCardSearchInput, $page: Int, $limit: Int) {
  BookCard(houseOfCultureId: $houseOfCultureId, search: $search, page: $page, limit: $limit) {
    total
    data {
      bookCardId
      name
      authors
      ageLimit
      isForeignAgent
      hasDrugsContent
      imageCoverObject { value }
    }
  }
}
"""

BOOK_DETAIL_QUERY = """
query BookDetail($search: BookCardSearchInput) {
  BookCard(search: $search) {
    data {
      bookCardId
      name
      authors
      genre
      annotation
      cityOfPublication
      publishingHouse
      yearOfPublishing
      numberOfPages
      ageLimit
      ebookUrl
      bibliographicDescription
      imageCoverObject { value }
      isForeignAgent
      hasDrugsContent
    }
  }
}
"""

AVAILABILITY_LIBRARIES_QUERY = """
query AvailabilityLibrariesList($bookCardId: [String], $urbanDistrictId: [String], $searchString: String) {
  BookCardLibraries(bookCardId: $bookCardId, urbanDistrictId: $urbanDistrictId, searchString: $searchString) {
    id: houseOfCultureId
    name
    address: shortAddress
    urbanDistrict { name: urbanDistrictName id: urbanDistrictId }
    slug
    workingWeekDays { from to isDayOff }
  }
}
"""


def run() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(CATALOG_URL, wait_until="networkidle", timeout=60000)

        def gql(op: str, query: str, variables: dict, retries: int = 5) -> dict:
            last_error: Exception | None = None
            for attempt in range(1, retries + 1):
                try:
                    res = page.request.post(
                        GRAPHQL_URL,
                        data={"operationName": op, "variables": variables, "query": query},
                        headers={"content-type": "application/json", "origin": "https://biblio.mosreg.ru", "referer": CATALOG_URL},
                    )
                    if res.status != 200:
                        raise RuntimeError(f"{op}: status {res.status}")
                    payload = res.json()
                    if payload.get("errors"):
                        raise RuntimeError(f"{op}: {payload['errors']}")
                    return payload["data"]
                except Exception as exc:
                    last_error = exc
                    if attempt < retries:
                        time.sleep(min(5, attempt))
            raise RuntimeError(str(last_error))

        districts = gql("SiteUrbanDistricts", URBAN_DISTRICTS_QUERY, {})["UrbanDistricts"]
        district_id = next(
            (d["id"] for d in districts if "балаш" in ((d.get("title", "") + " " + d.get("name", "")).lower())),
            None,
        )
        if not district_id:
            raise RuntimeError("Округ Балашиха не найден")

        houses: list[dict[str, str]] = []
        hp = 1
        while True:
            data = gql("HousesOfCultureSelectList", HOUSES_QUERY, {"limit": 100, "page": hp, "listUrbanDistrictId": [district_id]})[
                "HousesOfCulture"
            ]
            houses.extend(data["data"])
            if not data["has_more_pages"]:
                break
            hp += 1
        print(f"libraries_in_district={len(houses)}")

        categories = gql("CommonBooksCategories", BOOK_CATEGORIES_QUERY, {})["BookCategory"]["data"]
        print(f"book_categories={len(categories)}")

        books_by_id: dict[str, dict[str, Any]] = {}
        unresolved_buckets: list[dict[str, Any]] = []
        sum_house_totals = 0

        def fetch_bucket(house_id: str, category_id: str, prefix: str) -> None:
            search: dict[str, Any] = {"bookCategoryId": [category_id]}
            if prefix:
                search["name"] = prefix

            root = gql("BookCatalog", BOOK_CATALOG_QUERY, {"houseOfCultureId": [house_id], "search": search, "page": 1, "limit": PAGE_LIMIT})[
                "BookCard"
            ]
            total = int(root.get("total") or 0)
            if total == 0:
                return

            if total > MAX_BUCKET and len(prefix) < MAX_PREFIX_DEPTH:
                for ch in CHARSET:
                    fetch_bucket(house_id, category_id, prefix + ch)
                return

            if total > MAX_BUCKET:
                unresolved_buckets.append({"houseId": house_id, "categoryId": category_id, "prefix": prefix, "total": total})

            pages = max(1, min(10, math.ceil(total / PAGE_LIMIT)))
            for pg in range(1, pages + 1):
                page_data = gql(
                    "BookCatalog",
                    BOOK_CATALOG_QUERY,
                    {"houseOfCultureId": [house_id], "search": search, "page": pg, "limit": PAGE_LIMIT},
                )["BookCard"]
                for row in page_data.get("data", []):
                    bid = row.get("bookCardId")
                    if not bid:
                        continue
                    if bid not in books_by_id:
                        books_by_id[bid] = {
                            "bookCardId": bid,
                            "title": row.get("name"),
                            "author": row.get("authors"),
                            "ageLimit": row.get("ageLimit"),
                            "isForeignAgent": row.get("isForeignAgent"),
                            "hasDrugsContent": row.get("hasDrugsContent"),
                            "coverUrl": (row.get("imageCoverObject") or {}).get("value"),
                        }

        for hi, house in enumerate(houses, start=1):
            house_id = house["houseOfCultureId"]
            house_total = gql("BookCatalog", BOOK_CATALOG_QUERY, {"houseOfCultureId": [house_id], "search": {}, "page": 1, "limit": PAGE_LIMIT})[
                "BookCard"
            ]["total"]
            sum_house_totals += int(house_total or 0)

            for cat in categories:
                fetch_bucket(house_id, cat["id"], "")

            print(f"house={hi}/{len(houses)} unique_books={len(books_by_id)}")

        all_ids = list(books_by_id.keys())
        books: list[dict[str, Any]] = []
        for idx, bid in enumerate(all_ids, start=1):
            base = books_by_id[bid]
            detail_data = gql("BookDetail", BOOK_DETAIL_QUERY, {"search": {"bookCardId": [bid]}})
            detail = ((detail_data.get("BookCard") or {}).get("data") or [{}])[0]

            libs_data = gql(
                "AvailabilityLibrariesList",
                AVAILABILITY_LIBRARIES_QUERY,
                {"bookCardId": bid, "urbanDistrictId": [district_id]},
            )
            libs = libs_data.get("BookCardLibraries") or []
            branches = [
                {
                    "id": lib.get("id"),
                    "name": lib.get("name"),
                    "address": lib.get("address"),
                    "urbanDistrict": (lib.get("urbanDistrict") or {}).get("name"),
                    "slug": lib.get("slug"),
                    "workingWeekDays": lib.get("workingWeekDays") or [],
                    # Публичный API не отдает фактическое количество экземпляров.
                    "copies": None,
                }
                for lib in libs
            ]

            books.append(
                {
                    "bookCardId": bid,
                    "title": detail.get("name") or base.get("title"),
                    "author": detail.get("authors") or base.get("author"),
                    "annotation": detail.get("annotation"),
                    "description": detail.get("bibliographicDescription"),
                    "genre": detail.get("genre"),
                    "publishingHouse": detail.get("publishingHouse"),
                    "cityOfPublication": detail.get("cityOfPublication"),
                    "yearOfPublishing": detail.get("yearOfPublishing"),
                    "numberOfPages": detail.get("numberOfPages"),
                    "ebookUrl": detail.get("ebookUrl"),
                    "ageLimit": detail.get("ageLimit") or base.get("ageLimit"),
                    "isForeignAgent": detail.get("isForeignAgent"),
                    "hasDrugsContent": detail.get("hasDrugsContent"),
                    "coverUrl": ((detail.get("imageCoverObject") or {}).get("value")) or base.get("coverUrl"),
                    "branches": branches,
                }
            )
            if idx % 100 == 0 or idx == len(all_ids):
                print(f"details={idx}/{len(all_ids)}")

        payload = {
            "district": TARGET_DISTRICT,
            "districtId": district_id,
            "source": CATALOG_URL,
            "graphqlEndpoint": GRAPHQL_URL,
            "libraries_in_district": len(houses),
            "sum_of_house_catalog_totals": sum_house_totals,
            "total_books": len(books),
            "note": (
                "Сбор выполняется через разбиение на категории и префиксы названий, чтобы обходить лимит API 1000 записей на выборку. "
                "Заполнены детальные поля книги и список филиалов. "
                "Количество экземпляров (copies) публичный API не возвращает."
            ),
            "unresolved_buckets": unresolved_buckets,
            "books": books,
        }
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        browser.close()

    print(f"Готово: {OUTPUT_FILE}")


if __name__ == "__main__":
    run()
