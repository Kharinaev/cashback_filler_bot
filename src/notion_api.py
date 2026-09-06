from datetime import datetime

from notion_client import Client


class NotionDB:
    def __init__(self, notion_api, db_id):
        self.client = Client(auth=notion_api)
        self.db_id = db_id
        self.required_fields = ["Category", "Percent", "Bank", "Person", "Date"]
        self.additional_fields = ["Limit, ₽", "Info"]

    def get_unique_categories(self):
        database = self.client.databases.retrieve(database_id=self.db_id)
        category_names = [
            cat["name"]
            for cat in database["properties"]["Category"]["multi_select"][
                "options"
            ]
        ]

        return category_names

    def get_current_month_rows(self):
        """Return all cashback rows dated within the current month."""
        month_start = datetime.now().replace(day=1).strftime("%Y-%m-%d")

        rows = []
        start_cursor = None
        while True:
            query = {
                "database_id": self.db_id,
                "filter": {
                    "property": "Date",
                    "date": {"on_or_after": month_start},
                },
            }
            if start_cursor:
                query["start_cursor"] = start_cursor

            response = self.client.databases.query(**query)

            for page in response["results"]:
                props = page["properties"]

                categories = [
                    cat["name"] for cat in props["Category"]["multi_select"]
                ]
                bank = props["Bank"]["select"]
                person = props["Person"]["select"]

                rows.append(
                    {
                        "Category": categories[0] if categories else None,
                        "Percent": props["Percent"]["number"],
                        "Bank": bank["name"] if bank else None,
                        "Person": person["name"] if person else None,
                    }
                )

            if not response.get("has_more"):
                break
            start_cursor = response.get("next_cursor")

        return rows

    def check_row_data(self, row_data):
        for field in self.required_fields:
            if field not in row_data:
                raise ValueError(f'No "{field}" specified: {row_data}')

        for field in self.additional_fields:
            if field not in row_data:
                row_data[field] = None

    def add_row_to_database(self, row_data):
        self.check_row_data(row_data)
        properties = {}

        for key, value in row_data.items():
            if key == "Category":
                properties[key] = {
                    "multi_select": [{"name": value}] if value else []
                }
            elif key == "Limit, ₽" or key == "Percent":
                properties[key] = {"number": float(value) if value else None}
            elif key == "Bank" or key == "Person":
                properties[key] = {"select": {"name": value} if value else None}
            elif key == "Date":
                properties[key] = {"date": {"start": value} if value else None}
            elif key == "Info":
                properties[key] = {
                    "title": (
                        [{"text": {"content": value}}]
                        if value
                        else [{"text": {"content": ""}}]
                    )
                }

        new_page = {
            "parent": {"database_id": self.db_id},
            "properties": properties,
        }

        response = self.client.pages.create(**new_page)
        return response
