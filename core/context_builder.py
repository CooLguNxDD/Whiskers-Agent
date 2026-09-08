"""
Builder class to manage context state for the fastMCP app.
"""
from fastmcp import FastMCP

class ContextBuilder:
    """
    Builder class for creating execution contexts.
    """
    def __init__(self, base_instructions: str):
        """Initialize ContextBuilder with base instructions."""
        self.instructions = [base_instructions]
    
    def add_context(self, context_str: str):
        """Append a context string to the list of instructions if not already present."""
        if not context_str:
            return
        stripped = context_str.strip()
        if stripped and stripped not in self.instructions:
            self.instructions.append(stripped)
            
    def get_instructions(self) -> str:
        """Combine all stored instructions into a single string."""
        return "\n\n".join(self.instructions)
        
    def update_mcp_instructions(self, mcp_app: FastMCP):
        """Update the FastMCP application instructions with the combined instructions."""
        mcp_app.instructions = self.get_instructions()
