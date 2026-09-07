import argparse

import yaml
from notion_client import Client
from src.google_sheets_api import GoogleSheetsDB


def plain_text(prop):
    values = prop.get("title") or prop.get("rich_text") or []
    return "".join(item.get("plain_text", "") for item in values)


def number_value(prop):
    value = prop.get("number")
    return "" if value is None else value


def export_notion_rows(client, database_id):
    rows = []
    cursor = None
    while True:
        query = {"database_id": database_id}
        if cursor:
            query["start_cursor"] = cursor
        response = client.databases.query(**query)
        for page in response.get("results", []):
            props = page["properties"]
            categories = props["Category"]["multi_select"]
            bank = props["Bank"]["select"]
            person = props["Person"]["select"]
            date = props["Date"]["date"]
            rows.append(
                {
                    "Category": (categories[0]["name"] if categories else ""),
                    "Percent": number_value(props["Percent"]),
                    "Bank": bank["name"] if bank else "",
                    "Person": person["name"] if person else "",
                    "Date": date["start"] if date else "",
                    "Limit, ₽": number_value(props["Limit, ₽"]),
                    "Info": plain_text(props["Info"]),
                }
            )
        if not response.get("has_more"):
            return rows
        cursor = response["next_cursor"]


def export_notion_categories(client, database_id):
    database = client.databases.retrieve(database_id=database_id)
    options = database["properties"]["Category"]["multi_select"]["options"]
    return [option["name"] for option in options]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--notion-config", required=True)
    parser.add_argument("--google-key", required=True)
    parser.add_argument("--spreadsheet-id", required=True)
    args = parser.parse_args()

    with open(args.notion_config, "r") as config_file:
        config = yaml.safe_load(config_file)

    notion = Client(auth=config["db"]["api_key"])
    database_id = config["db"]["db_id"]
    rows = export_notion_rows(notion, database_id)
    categories = export_notion_categories(notion, database_id)

    sheets = GoogleSheetsDB(
        credentials_file=args.google_key,
        spreadsheet_id=args.spreadsheet_id,
    )
    sheets.replace_categories(categories)
    sheets.replace_rows(rows)
    print(f"Migrated rows: {len(rows)}")
    print(f"Migrated categories: {len(categories)}")


if __name__ == "__main__":
    main()
