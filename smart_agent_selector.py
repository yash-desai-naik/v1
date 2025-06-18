import re
import json
import asyncio
import hashlib
import sys
from typing import Dict, Any, List, Tuple

try:
    from agno.models.openai import OpenAIChat
    from agno.agent import Agent
    from composio_agno import ComposioToolSet, Action
    from agno.team.team import Team
    from agno.tools.mcp import MCPTools
except ImportError as e:
    print(f"❌ Missing dependencies: {e}")
    print("Please install required packages:")
    print("pip install agno composio-agno openai mcp")
    sys.exit(1)

# Global cache and lock
_selector_cache = {}
_cache_lock = asyncio.Lock()

# Precompiled regex patterns for rule-based matching
RULE_PATTERNS = [
    # Gmail patterns
    (r"^\s*(check|read|show|get|view) (my )?(email|emails|gmail|inbox|messages)\b", ["gmail"], False),
    (r"\b(unread|new) emails?\b", ["gmail"], False),
    
    # Calendar patterns
    (r"\b(calendar|schedule|agenda|appointments?|events?|meetings?)\b", ["googlecalendar"], False),
    (r"\bwhat('?s| is) on (my )?(calendar|schedule)", ["googlecalendar"], False),
    (r"\b(add|create|schedule) (a )?(event|meeting|appointment)\b", ["googlecalendar"], False),
    
    # Weather patterns
    (r"\b(weather|forecast|temperature|humidity|rain|snow|wind|sunny)\b", ["weather"], False),
    (r"\bhow is the weather (in )?.*", ["weather"], False),
    
    # Search patterns
    (r"\b(search|find|look up|research|google|web) (for )?.*", ["composio_search"], False),
    (r"\b(who is|what is|where is) .*", ["composio_search"], False),
    
    # Drive patterns
    (r"\b(drive|document|spreadsheet|doc|sheet|presentation|slide)\b", ["googledrive"], False),
    (r"\b(open|create|edit|share) (a )?(doc|document|sheet|spreadsheet)\b", ["googledrive"], False),
    
    # Maps patterns
    (r"\b(map|maps|location|route|directions|navigate|distance)\b", ["google_maps"], False),
    (r"\bhow (to|do I) get to .*", ["google_maps"], False),
    
    # Slack patterns
    (r"\b(slack|message|chat|channel|workspace|dm|direct message)\b", ["slack"], False),
    (r"\b(send|post) (a )?(message|notification) (in|to) .*", ["slack"], False),
    
    # Filesystem triggers
    (r"\b(file|files|folder|directory|document|save|store|download|upload|attach)\b", [], True),
]

# Precompiled regex objects
COMPILED_RULES = [(re.compile(pattern, re.IGNORECASE), agents, fs) 
                  for pattern, agents, fs in RULE_PATTERNS]

# Common requests for pre-caching
COMMON_REQUESTS = [
    "check my emails",
    "what's on my calendar today?",
    "weather forecast",
    "search for AI news",
    "directions to central park",
    "save this document",
    "send a slack message",
    "create a spreadsheet",
    "navigate to airport",
    "download the file"
]

def rule_based_selector(user_request: str) -> Tuple[List[str], bool]:
    """Ultra-fast rule-based selector with pattern matching"""
    request_lower = user_request.lower()
    agents = set()
    needs_filesystem = False
    
    # Apply all regex rules
    for pattern, pattern_agents, pattern_fs in COMPILED_RULES:
        if pattern.search(request_lower):
            for agent in pattern_agents:
                agents.add(agent)
            if pattern_fs:
                needs_filesystem = True
    
    # Contextual overrides
    if "search" in request_lower and "save" in request_lower:
        agents.add("composio_search")
        needs_filesystem = True
        
    if "weather" in agents and ("map" in request_lower or "directions" in request_lower):
        agents.add("google_maps")
    
    return list(agents), needs_filesystem

async def llm_based_selector(user_request: str, model) -> Dict[str, Any]:
    """Optimized LLM-based selector with caching and timeouts"""
    # Generate request signature for caching
    request_hash = hashlib.sha256(user_request.encode()).hexdigest()
    
    # Check cache
    async with _cache_lock:
        if request_hash in _selector_cache:
            return _selector_cache[request_hash]
    
    # Optimized prompt for speed
    prompt = (
        "Available agents: gmail,googlecalendar,weather,composio_search,"
        "googledrive,google_maps,slack,filesystem\n"
        "Respond ONLY with JSON. Example: "
        '{"agents":["gmail"],"needs_filesystem":false}\n'
        f"Request: {user_request}"
    )
    
    # Create agent with optimized settings
    selector = Agent(
        name="Hybrid_Selector_LLM",
        model=model,
        instructions=[prompt],
        # max_tokens=80,
        # temperature=0.0,
        # response_format={"type": "json_object"}
        # format=
    )
    
    # Execute with timeout
    try:
        response = await asyncio.wait_for(
            selector.arun(prompt),
            timeout=1.0  # Aggressive timeout
        )
        result = json.loads(response.content.strip())
    except (asyncio.TimeoutError, json.JSONDecodeError):
        # Fallback to rule-based if LLM fails
        agents, needs_fs = rule_based_selector(user_request)
        result = {"agents": agents or ["composio_search"], 
                 "needs_filesystem": needs_fs}
    
    # Update cache
    async with _cache_lock:
        _selector_cache[request_hash] = result
        
    return result

async def smart_agent_selector(user_request: str, model) -> Dict[str, Any]:
    """Hybrid agent selector with rule-based priority"""
    # First try ultra-fast rule-based approach
    agents, needs_fs = rule_based_selector(user_request)
    
    # If rule-based found agents and request is simple, use it
    if agents and len(user_request.split()) <= 7:
        return {"agents": agents, "needs_filesystem": needs_fs}
    
    # Otherwise use optimized LLM with caching
    return await llm_based_selector(user_request, model)

async def warmup_cache(model):
    """Pre-cache common requests during initialization"""
    warmup_tasks = [smart_agent_selector(req, model) for req in COMMON_REQUESTS]
    await asyncio.gather(*warmup_tasks, return_exceptions=True)

# Initialize with fast model during app startup
# asyncio.run(warmup_cache("gpt-3.5-turbo"))