"""Claims workflow (returns, refunds, skin reactions) as a LangGraph state machine.

Why LangGraph here and ADK for the chat: the conversation is open-ended (the model decides what to do next);
a claim is a REGULATED PROCESS: the steps, the rules and who decides are fixed, every transition must be auditable,
a human must be able to approve days later, and a failed refund must resume without paying twice. That is explicit
state + checkpoints + interrupts, which is what LangGraph is built for.
"""
