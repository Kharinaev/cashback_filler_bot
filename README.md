# Cashback Autocomplete Bot

A Telegram bot that automates the process of categorizing and recording
cashbacks using Vision Language Models (VLM) and Google Sheets.

## Features

- 📸 Process cashback screenshot automatically
- ➕ Add cashback categories manually with guided Telegram buttons
- 📅 Choose a fixed working month or return to the current month automatically
- 🔎 Find similar categories in the current month from any text message
- 💳 Manage card last-four digits and show them in lists and search results
- 🧹 Skip exact duplicate cashback rows
- 🤖 VLM-powered transaction categorization
- 📊 Integration with Google Sheets
- 📱 Telegram interface
- 💰 Cashback tracking and management

## Prerequisites

- Python 3.8+
- Telegram Bot Token
- Google Cloud service account key
- Google Spreadsheet ID
- VLM API access

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/cashback_autocomplete.git
cd cashback_autocomplete
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set up your environment variables or create a configuration file in `configs/` directory.

## Configuration

Create a configuration file (e.g., `configs/config.yaml`) with the following structure:

```yaml
db:
  credentials_file: "configs/google-service-account.json"
  spreadsheet_id: "your-google-spreadsheet-id"
  cashbacks_sheet: "Cashbacks"
  categories_sheet: "Categories"
  cards_sheet: "Cards"

vlm:
  base_url: "your-vlm-api-url"
  api_token: "your-vlm-api-token"
  model_name: "your-model-name"
  prompt_template_file: "path/to/prompt/template"
  sampling_params:
    # Add your VLM sampling parameters here

telegram:
  token: "your-telegram-bot-token"
  allowed_users:
    - "username1"
    - "username2"
```

## Usage

1. Start the bot:
```bash
python run_bot.py
```

2. In Telegram:
   - Start a chat with your bot
   - Send `/start` to begin
   - Upload receipt images
   - Follow the bot's prompts to confirm or modify categorizations

## Project Structure

```
cashback_autocomplete/
├── src/
│   ├── bot.py          # Telegram bot implementation
│   ├── pipe.py         # Main processing pipeline
│   ├── vlm.py          # Vision Language Model integration
│   ├── google_sheets_api.py  # Google Sheets interaction
│   └── tools.py        # Utility functions
├── configs/            # Configuration files
├── data/              # Data storage
├── notebooks/         # Jupyter notebooks
├── tests/             # Test files
└── artifacts/         # Generated artifacts
```

## Spreadsheet structure

The `Cashbacks` sheet uses these columns in order:

`Category | Percent | Bank | Person | Date | Limit, ₽ | Info`

The `Categories` sheet is the editable dropdown reference table:

`Category | Person | Bank`

The `Cards` sheet stores only card last-four digits:

`Person | Bank | Card`

To refresh dropdown validation and visual formatting after changing the
spreadsheet structure, run:

```bash
python -m scripts.setup_google_sheet_ui --config configs/config.yaml
```

The setup script also applies strict dropdowns and value colors for category,
person, and bank columns. Percent and date formatting are left unchanged.

## Development

### Setting up development environment

1. Install pre-commit hooks:
```bash
pre-commit install
```

2. Run tests:
```bash
pytest
```

### Code Style

This project uses:
- Black for code formatting
- isort for import sorting
- flake8 for linting

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

- Telegram Bot API
- Google Sheets API
- Vision Language Models
- All contributors and maintainers
