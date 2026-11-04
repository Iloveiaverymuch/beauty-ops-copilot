import os

from google.adk.agents import Agent

from copilot.tools.catalog import get_campaign, search_products

catalog_agent = Agent(
    name="catalog_agent",
    model=os.getenv("MODEL", "gemini-3.8-flash"),
    # `description` is what a PARENT agent reads to decide routing. `instruction` is what THIS agent obeys.
    description="Answers questions about products (SKUs, ingredients, prices) and marketing campaigns.",
    instruction=(
        "You answer ONLY from tool results; never from memory.\n"
        "- Always cite SKUs or campaign ids.\n"
        "- If a tool returns count 0 or an error, say so plainly and suggest a refined query.\n"
        "- Never invent prices, ingredients, budgets or owners.\n"
        "- Use the fewest tool calls: put all constraints (brand, category, price, quarter) in ONE call. "
        "Do not re-query to double-check a result."
    ),
    tools=[search_products, get_campaign],  # plain functions -> ADK wraps them in FunctionTool
)
