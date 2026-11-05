import os

from google.adk.agents import Agent

from copilot.model import build_model

from copilot.tools.catalog import get_campaign, search_products

DESCRIPTION = "Answers questions about products (SKUs, ingredients, prices) and marketing campaigns (budgets, owners, hero SKUs)."
INSTRUCTION = (
    "You answer ONLY from tool results; never from memory.\n"
    "- Always cite SKUs or campaign ids.\n"
    "- If a tool returns count 0 or an error, say so plainly; if it returns `near_miss`, mention it.\n"
    "- Never invent prices, ingredients, budgets or owners.\n"
    "- Use the fewest tool calls: put all constraints (brand, category, price, quarter) in ONE call. "
    "Do not re-query to double-check a result."
)


def build_catalog_agent(name: str = "catalog_agent") -> Agent:
    # Factory, not a singleton: an ADK agent instance can have only ONE parent in the tree.
    return Agent(
        name=name,
        model=build_model(),
        # `description` is what a PARENT (router or AgentTool caller) reads to decide. `instruction` is what THIS agent obeys.
        description=DESCRIPTION,
        instruction=INSTRUCTION,
        tools=[search_products, get_campaign],  # plain functions -> ADK wraps them in FunctionTool
    )


catalog_agent = build_catalog_agent()
