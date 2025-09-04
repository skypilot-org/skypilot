# Linear to Notion Sync for Skypilot Issues

This tool automatically syncs Linear issues assigned to you (in "Todo" state) to Notion, analyzing the skypilot codebase to provide proposed fixes.

## 🚀 Quick Start

1. **Run the setup script:**
   ```bash
   ./setup_linear_notion_sync.sh
   ```

2. **Configure your API keys in `.env`:**
   ```bash
   # Edit the .env file
   nano .env
   ```

3. **Run the sync:**
   ```bash
   python3 linear_notion_sync.py
   ```

## 📋 Prerequisites

### Linear API Key
1. Go to [Linear Settings > API](https://linear.app/settings/api)
2. Click "Create new API key"
3. Give it a name (e.g., "Notion Sync")
4. Copy the generated key

### Notion Integration
1. Go to [Notion Integrations](https://www.notion.so/my-integrations)
2. Click "New integration"
3. Give it a name (e.g., "Linear Sync")
4. Select the workspace
5. Copy the "Internal Integration Token"

### Notion Database Setup
1. Create a database/page in Notion for your issues (or use existing)
2. Share the database with your integration:
   - Click "..." menu > "Add connections"
   - Search for your integration name
   - Click "Confirm"
3. Get the database ID from the URL:
   ```
   https://www.notion.so/workspace/[DATABASE_ID_HERE]?v=...
   ```

## 🛠 Features

The script will:
- ✅ Fetch all Linear issues assigned to you in "Todo" state
- ✅ Analyze the skypilot codebase for relevant code
- ✅ Create detailed Notion pages with:
  - Issue details (title, description, labels, priority)
  - Comments from Linear
  - Relevant code locations
  - Proposed fixes based on code analysis
  - Implementation strategies
  - Testing recommendations

## 📝 Notion Page Structure

Each created Notion page includes:

### Issue Information
- Title with Linear issue identifier
- Team and project information
- Priority and labels
- Full description
- Comments history

### Code Analysis
- Relevant file locations
- Code snippets with context
- Keywords found in the codebase

### Proposed Fix
- Issue type analysis (bug, feature, optimization)
- Step-by-step solution approach
- Implementation details
- Testing strategy

## 🔧 Customization

### Modify Issue Filters
To fetch different issues, edit the GraphQL query in `LinearClient.get_assigned_todo_issues()`:
```python
# Change the state filter
filter: { state: { name: { eq: "In Progress" } } }
```

### Adjust Code Analysis
The `SkypilotAnalyzer` class can be customized:
- Add more file types to search
- Modify keyword extraction logic
- Enhance the proposed fix generation

### Notion Properties
Add custom properties to the Notion pages by modifying the `create_page()` method.

## 🐛 Troubleshooting

### "API key not set" Error
Make sure your `.env` file contains all required keys:
```env
LINEAR_API_KEY=lin_api_...
NOTION_API_KEY=secret_...
NOTION_DATABASE_ID=...
```

### "Notion API error: 400"
- Ensure the integration has access to the database
- Check that the database ID is correct
- Verify the Notion API version is compatible

### No issues found
- Verify you have issues assigned to you in Linear
- Check that the issues are in "Todo" state
- Try the Linear API in their playground first

## 📚 Additional Notes

- The script limits code snippets to prevent Notion API limits
- Large issues may be truncated to fit Notion's block limits
- File search is limited to Python files by default
- The analysis focuses on the `/sky` directory in skypilot

## 🤝 Contributing

To improve the analysis:
1. Enhance keyword extraction in `_extract_keywords()`
2. Add more file type support in `_find_relevant_files()`
3. Improve fix suggestions in `_generate_proposed_fix()`

## 📄 License

This tool is provided as-is for syncing Linear issues to Notion with skypilot code analysis.