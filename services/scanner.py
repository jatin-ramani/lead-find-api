from geoapify import search_business


def scan_city(city, category):
    print(f"\nScanning {city}...\n")

    search_business(
        city=city,
        category=category
    )


if __name__ == "__main__":
    scan_city(
        city="Ahmedabad",
        category="commercial"
    )