import json
import csv
import argparse
from pathlib import Path

def normalize_name(name):
    """Normalize tool names by removing underscores and making lowercase to match CSV to JSON."""
    return name.lower().replace("_", "")

def main():
    parser = argparse.ArgumentParser(description="Strip useless tools using a CSV toolkit whitelist")
    parser.add_argument("--csv", required=True, help="Path to Toolkit.csv")
    parser.add_argument("--input", required=True, help="Path to mcp-tools-ai.json")
    parser.add_argument("--out", required=True, help="Path to output mcp-tools-ai-stripped.json")
    args = parser.parse_args()

    csv_file = Path(args.csv)
    json_file = Path(args.input)
    output_file = Path(args.out)

    # Read the allowed tools from the CSV
    allowed_tools = set()
    try:
        with open(csv_file, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # The column name from the CSV is 'Tool Name'
                tool_name = row.get("Tool Name")
                if tool_name:
                    allowed_tools.add(normalize_name(tool_name))
        print(f"Loaded {len(allowed_tools)} tools from {csv_file.name}")
    except Exception as e:
        print(f"Error reading CSV: {e}")
        return

    # Read the original JSON
    try:
        with open(json_file, mode='r', encoding='utf-8') as f:
            tools_data = json.load(f)
        print(f"Loaded {len(tools_data)} total tools from {json_file.name}")
    except Exception as e:
        print(f"Error reading JSON: {e}")
        return

    # Filter the tools
    filtered_tools = []
    for tool in tools_data:
        tool_name = tool.get("name", "")
        if normalize_name(tool_name) in allowed_tools:
            filtered_tools.append(tool)

    # Write the stripped JSON
    try:
        with open(output_file, mode='w', encoding='utf-8') as f:
            json.dump(filtered_tools, f, indent=2)
        print(f"Successfully wrote {len(filtered_tools)} tools to {output_file.name}")
    except Exception as e:
        print(f"Error writing output JSON: {e}")

if __name__ == "__main__":
    main()
