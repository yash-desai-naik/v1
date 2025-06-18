#!/usr/bin/env python3
"""
Ubik AI - Standalone CLI App
A simple AI assistant that connects to your Gmail, Calendar, Drive, Slack and more.
"""
import asyncio
import argparse
import os
import sys
import json
from typing import Dict, Any, List, Optional

from mcp import StdioServerParameters
from ubik_tools import gmail_tools_actions, calendar_tools_actions, \
    googledrive_tools_actions, weather_tools_actions, websearch_tools_actions, \
    google_maps_tools_actions, slack_tools_actions

try:
    from agno.models.openai import OpenAIChat
    from agno.agent import Agent
    from composio_agno import ComposioToolSet, Action
    from agno.team.team import Team
    from agno.tools.mcp import MCPTools
    from agno.memory.v2.memory import Memory
    from agno.memory.v2.db.sqlite import SqliteMemoryDb
    from agno.storage.sqlite import SqliteStorage
except ImportError as e:
    print(f"❌ Missing dependencies: {e}")
    print("Please install required packages:")
    print("pip install agno composio-agno openai mcp")
    sys.exit(1)


# from smart_agent_selector import smart_agent_selector

# Tool actions mapping
TOOL_ACTIONS = {
    "gmail": gmail_tools_actions,
    "googlecalendar": calendar_tools_actions,
    "googledrive": googledrive_tools_actions,
    "slack": slack_tools_actions,
    "weathermap": weather_tools_actions,
    "composio_search": websearch_tools_actions,
    "google_maps": google_maps_tools_actions,
}

# Apps that need OAuth
OAUTH_APPS = ["gmail", "googlecalendar", "googledrive", "slack", 'google_maps']
NO_AUTH_APPS = ["weathermap", "composio_search"]


def print_banner():
    """Print app banner"""
    print("🤖 Ubik AI - Your Personal Assistant")
    print("=" * 50)


async def smart_agent_selector(user_request: str, model) -> Dict[str, Any]:
    """AI decides which agents are needed"""
    selector = Agent(
        name="Agent Selector",
        model=model,
        instructions=[
            "Decide which agents are needed for a user request. Respond ONLY with JSON.",
            "Available agents: gmail, googlecalendar, weather, composio_search, googledrive, google_maps, slack, filesystem",
            'Format: {"agents": ["agent1", "agent2"], "needs_filesystem": false}',
            "",
            "Examples:",
            '"check emails" -> {"agents": ["gmail"], "needs_filesystem": false}',
            '"schedule today" -> {"agents": ["googlecalendar"], "needs_filesystem": false}',
            '"weather forecast" -> {"agents": ["weather"], "needs_filesystem": false}',
            '"search for news" -> {"agents": ["composio_search"], "needs_filesystem": false}',
            '"web search and save" -> {"agents": ["composio_search"], "needs_filesystem": true}',
        ],
    )
    
    response = await selector.arun(f"Which agents needed for: '{user_request}'")
    
    try:
        return json.loads(response.content.strip())
    except:
        return {"agents": ["search"], "needs_filesystem": False}


def check_connections(toolset: ComposioToolSet, entity_id: str, needed_agents: List[str]) -> Dict[str, bool]:
    """Check which agents have valid connections"""
    connection_status = {}
    
    for agent_type in needed_agents:
        if agent_type in OAUTH_APPS:
            try:
                entity = toolset.get_entity(entity_id)
                connections = entity.get_connections()
                
                app_mapping = {
                    "gmail": "gmail",
                    "googlecalendar": "googlecalendar", 
                    "googledrive": "googledrive",
                    "slack": "slack",
                    "google_maps": "google_maps",
                }
                
                app_name = app_mapping.get(agent_type, agent_type)
                
                is_connected = any(
                    getattr(conn, 'appName', '').lower() == app_name.lower() and
                    getattr(conn, 'status', '').lower() in ['active', 'connected']
                    for conn in connections
                )
                
                connection_status[agent_type] = is_connected
                
            except Exception:
                connection_status[agent_type] = False
        else:
            connection_status[agent_type] = True
    
    return connection_status


def list_all_apps(composio_api_key: str):
    """List all available apps"""
    print("📱 Available Apps:")
    print()

    oauth_apps = ["gmail", "googlecalendar", "googledrive", "slack", "google_maps"]
    no_auth_apps = ["weathermap", "composio_search", "desktop_commander"]
    
    for i, app in enumerate(oauth_apps, 1):
        print(f"{i}. {app} (needs oauth)")
    
    for i, app in enumerate(no_auth_apps, len(oauth_apps) + 1):
        print(f"{i}. {app} (no oauth) (no need to connect)")


def connect_app(app_name: str, entity_id: str, composio_api_key: str):
    """Connect to an OAuth app"""
    if app_name not in OAUTH_APPS:
        print(f"❌ {app_name} doesn't need authentication")
        return
    
    try:
        toolset = ComposioToolSet(api_key=composio_api_key, entity_id=entity_id)
        entity = toolset.get_entity(entity_id)
        
        # First check if already connected
        connections = entity.get_connections()
        for conn in connections:
            conn_app = getattr(conn, 'appName', '') or getattr(conn, 'app_name', '') or getattr(conn, 'app', '')
            if conn_app and str(conn_app).lower() == app_name.lower():
                conn_status = getattr(conn, 'status', '')
                if conn_status.lower() in ['active', 'connected']:
                    connection_id = getattr(conn, 'id', None) or getattr(conn, 'connectedAccountId', None)
                    print(f"✅ Already connected to {app_name}")
                    if connection_id:
                        print(f"📋 Connection ID: {connection_id}")
                    return
        
        # Not connected, initiate new connection
        connection_request = entity.initiate_connection(app_name=app_name)
        auth_url = getattr(connection_request, 'redirectUrl', None)
        
        if auth_url:
            print(f"🔗 Please authenticate {app_name}: {auth_url}")
        else:
            print(f"❌ Failed to get auth URL for {app_name}")
            
    except Exception as e:
        print(f"❌ Error connecting {app_name}: {e}")


def list_connected_apps(entity_id: str, composio_api_key: str):
    """List all connected apps"""
    try:
        toolset = ComposioToolSet(api_key=composio_api_key, entity_id=entity_id)
        entity = toolset.get_entity(entity_id)
        connections = entity.get_connections()
        
        print("📱 Connected Apps:")
        print()
        
        # Create a dict to track connected apps
        connected_apps = {}
        for conn in connections:
            conn_app = getattr(conn, 'appName', '') or getattr(conn, 'app_name', '') or getattr(conn, 'app', '')
            conn_status = getattr(conn, 'status', '')
            connection_id = getattr(conn, 'id', None) or getattr(conn, 'connectedAccountId', None)
            
            if conn_app:
                connected_apps[conn_app.lower()] = {
                    'status': conn_status,
                    'connection_id': connection_id,
                    'is_active': conn_status.lower() in ['active', 'connected']
                }
        
        all_apps = OAUTH_APPS + NO_AUTH_APPS
        
        for i, app in enumerate(all_apps, 1):
            if app in OAUTH_APPS:
                app_info = connected_apps.get(app.lower(), {})
                is_connected = app_info.get('is_active', False)
                status = "connected" if is_connected else "not connected"
                
                if is_connected and app_info.get('connection_id'):
                    print(f"{i}. {app} ({status}) - ID: {app_info['connection_id']}")
                else:
                    print(f"{i}. {app} ({status})")
            else:
                status = "connected"  # No auth needed
                print(f"{i}. {app} ({status})")
            
    except Exception as e:
        print(f"❌ Error listing connections: {e}")


async def create_dynamic_team(user_request: str, model:OpenAIChat, agent_selection_model:OpenAIChat, toolset: ComposioToolSet, memory: Memory, storage: SqliteStorage):
    """Create team dynamically based on AI selection"""
    
    # Get AI selection
    selection = await smart_agent_selector(user_request, agent_selection_model)
    needed_agents = selection.get("agents", [])
    needs_filesystem = selection.get("needs_filesystem", False)
    
    print(f"🎯 Selected agents: {', '.join(needed_agents)}" + 
          (f" + filesystem" if needs_filesystem else ""))
    
    # Map agent names to app names
    agent_to_app = {
        "gmail": "gmail",
        "googlecalendar": "googlecalendar",
        "googledrive": "googledrive",
        "slack": "slack",
        "weather": "weathermap",
        "composio_search": "composio_search"
    }
    
    # Create agents
    agents = []
    for agent_name in needed_agents:
        app_name = agent_to_app.get(agent_name, agent_name)
        
        if app_name in TOOL_ACTIONS:
            try:
                if app_name in OAUTH_APPS:
                    tools = toolset.get_tools(
                        actions=TOOL_ACTIONS[app_name], 
                        check_connected_accounts=True
                    )
                else:
                    tools = toolset.get_tools(actions=TOOL_ACTIONS[app_name])
                
                agent = Agent(
                    name=f"{agent_name.title()} Agent",
                    role=f"Handle {agent_name} tasks",
                    model=model,
                    instructions=[
                        f"Handle {agent_name} tasks efficiently.",
                        "Use timezone and local currency/units as per the user's location.",
                        f"user's current time is: {get_user_time()}",
                        f"user's timezone is: {system_timezone()}",
                        "** NOTE: DON'T PASS TIMEZONE WHILE CALLING GOOGLECALENDAR_FIND_EVENT",
                        "**NOTE: WHILE WORKING WITH GMAIL, ALWAYS GET THE EMAIL/MESSAGE ID FOR OPERATIONS",
                        "**NOTE: WHILE WORKING WITH GOOGLE DRIVE, ALWAYS GET THE FILE ID FOR OPERATIONS",
                        "**NOTE: WHILE WORKING WITH GMAIL ATTACHMENTS, USE GMAIL_GET_ATTACHMENT ACTION" ,
                    ],
                    tools=tools,
                    memory=memory,
                    storage=storage,
                    enable_agentic_memory=True,
                    enable_user_memories=True,
                    add_history_to_messages=True,
                    num_history_runs=3,
                    markdown=True,
                    add_datetime_to_instructions=True,
                    timezone_identifier=system_timezone()
                )
                # memory.clear()  # Clear memory for each agent
                agents.append(agent)
                
            except Exception as e:
                print(f"⚠️  Failed to create {agent_name} agent: {e}")
    
    # Add filesystem agent if needed
    if needs_filesystem:
        try:

            server_params = StdioServerParameters(
                command="npx",
                args=["-y", "@wonderwhy-er/desktop-commander@latest"],
                    )

            async with MCPTools(server_params=server_params) as desktop_commander:
                filesystem_agent = Agent(
                    name="Ubik File System Commander",
                    role="Professional DevOps File Operations Specialist",
                    model=model,
                    tools=[desktop_commander],
                    instructions=[
                        "You're an expert Linux/macOS command-line specialist with DevOps expertise.",
                        "Safety first: NEVER run destructive commands (rm -rf, dd, mv to root, etc.)",
                        f"Always create and use the 'Ubik AI' directory in the user's Desktop({get_home_directory()}/Desktop/Ubik AI) as default workspace:",
                        f"  1. Check existence: `ls {get_home_directory()}/Desktop/Ubik AI`",
                        f"  2. Create if missing: `mkdir -p {get_home_directory()}/Desktop/Ubik AI`",
                        "  3. Use as default path when user doesn't specify location",
                        "default path: ~/Desktop/Ubik AI or /Users/<username>/Desktop/Ubik AI",
                        "NOTE: If user doesn't specify a path, use the default Ubik AI directory on Desktop.",
                        "Professional standards:",
                        "  - Validate paths before operations",
                        "  - Use absolute paths with proper escaping",
                        "  - Confirm disk space before large operations",
                        "  - Preserve permissions and metadata",
                        "Special handling:",
                        "  - For media files: maintain originals, create copies for edits",
                        "  - For code: preserve git history and directory structure",
                        "User experience:",
                        "  - Provide clear progress indicators",
                        "  - Suggest optimizations (e.g., compression for large files)",
                        "  - Estimate time/resources for long operations",
                         f"user's current time is: {get_user_time()}",
                        f"user's timezone is: {system_timezone()}",
                        "** NOTE: Provide the path to file or folder if needed after completing the operation",
                    ],
                    add_datetime_to_instructions=True,
                    timezone_identifier=system_timezone(),
                    add_location_to_instructions=True,
                )
                
                # Create team with filesystem
                team = Team(
                    name="Ubik AI",
                    description='Your personal AI assistant',
                    mode="coordinate",
                    model=model,
                    members=agents + [filesystem_agent],
                    instructions=[
                        "Collaborate to provide comprehensive assistance",
                        "Use tools effectively to fetch and create information",
                        "Provide clear and actionable responses",
                        "Use markdown formatting for better readability",
                        "Use timezone and local currency/units as per the user's location.",
                        "When someone ask about you, tell them you are Ubik AI, a personal assistant that can help with various tasks like checking emails, scheduling events, searching the web(composio_search), and managing files. You're designed to assist users in their daily tasks and provide information quickly and efficiently. You're secure and respect user privacy",
                        f"user's timezone is: {system_timezone()} if you want to determine user's timezone, use `date` command or locaition information",
                        f"user's default directory is: {get_home_directory()}/Desktop/Ubik AI",
                        # "don't ask follow-up questions, just provide the answer",
                         f"user's current time is: {get_user_time()}",
                        f"user's timezone is: {system_timezone()}",
                    ],
                    markdown=True,
                    add_datetime_to_instructions=True,
                    add_location_to_instructions=True,
                    memory=memory,
                    # storage=storage,
                    enable_agentic_memory=True,
                    enable_user_memories=True,
                    add_history_to_messages=True,
                    num_history_runs=3,
                )
                
                print(f"👥 Team created with {len(agents)+1} agents")
                
                # Stream response
                response_stream = await team.arun(user_request, stream=True)
                
                print("\n==RESPONSE==")
                async for event in response_stream:
                    if event.event == "TeamRunResponseContent":
                        print(event.content, end="", flush=True)
                    elif event.event == "TeamToolCallStarted":
                        # print(f"\n🔧 Tool call started: {event.tool}")
                        ...
        
        except Exception as e:
            print(f"❌ Error with filesystem agent: {e}")
            needs_filesystem = False
    
    if not needs_filesystem:
        # Create team without filesystem
        team = Team(
            name="Ubik AI", 
            description='Your personal AI assistant',
            mode="coordinate",
            model=model,
            members=agents,
            instructions=[
                "Collaborate to provide comprehensive assistance",
                "Use tools effectively to fetch and create information",
                "Provide clear and actionable responses", 
                "Use markdown formatting for better readability",
                "Use timezone and local currency/units as per the user's location.",
                "When someone ask about you, tell them you are Ubik AI, a personal assistant that can help with various tasks like checking emails, scheduling events, searching the web(composio_search), and managing files. You're designed to assist users in their daily tasks and provide information quickly and efficiently. You're secure and respect user privacy",
                f"user's timezone is: {system_timezone()} if you want to determine user's timezone, use `date` command or locaition information",
                f"user's default directory is: {get_home_directory()}/Desktop/Ubik AI",
                f"user's current time is: {get_user_time()}",
                f"user's timezone is: {system_timezone()}",
            ],
            markdown=True,
            add_datetime_to_instructions=True,
            add_location_to_instructions=True,
            memory=memory,
            # storage=storage,
            enable_agentic_memory=True,
            enable_user_memories=True,
            add_history_to_messages=True,
            num_history_runs=3,
            enable_session_summaries=True,

        )
        
        print(f"👥 Team created with {len(agents)} agents")
        
        # Stream response
        response_stream = await team.arun(user_request, stream=True)
        
        print("\n==RESPONSE==")
        async for event in response_stream:
            if event.event == "TeamRunResponseContent":
                print(event.content, end="", flush=True)
            elif event.event == "TeamToolCallStarted":
                # print(f"\n🔧 Tool call started: {event.tool}")
                ...


async def process_query(user_request: str, entity_id: str, openai_key: str, composio_api_key: str, model:OpenAIChat, agent_selection_model:OpenAIChat, memory: Memory, storage: SqliteStorage):
    """Process a user query"""
    try:
        
        toolset = ComposioToolSet(api_key=composio_api_key, entity_id=entity_id)
        
        print("🤖 Processing your request...")
        print("=" * 50)

        await create_dynamic_team(user_request, model, agent_selection_model, toolset, memory, storage)

        print("\n" + "=" * 50)
        print("✅ Completed!")
        
    except Exception as e:
        print(f"❌ Error: {e}")



# get user's home directory
def get_home_directory() -> str:
    """Get the home directory of the current user."""
    return os.path.expanduser("~")

# get user's time
def get_user_time() -> str:
    """Get the current time in the user's timezone."""
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# get user's timezone
def system_timezone() -> str:
    """Get the system timezone."""
    
    #determine of os
    if os.name == 'posix':  # Unix-like systems (Linux, macOS)
        # return os.popen('date +%Z').read().strip()
        # use readlink /etc/localtime | sed 's|.*/zoneinfo/||'
        try:
            return os.readlink('/etc/localtime').split('zoneinfo/')[1]
        except Exception as e:
            print(f"Error getting timezone: {e}")
            return "Unknown"
    elif os.name == 'nt':  # Windows
        return os.popen('tzutil /g').read().strip()
    else:
        raise NotImplementedError("Unsupported operating system")

def main():
    """Main CLI function"""
    parser = argparse.ArgumentParser(
        description="Ubik AI - Your Personal Assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  ubik --query="what's the weather?" --entity_id=john@doe.com --openai_key=sk-xxx --composio_api_key=xxx
  ubik --list_apps --composio_api_key=xxx
  ubik --connect_app=gmail --entity_id=john@doe.com --composio_api_key=xxx
  ubik --list_connected_apps --entity_id=john@doe.com --composio_api_key=xxx
        """
    )
    
    # Main actions (mutually exclusive)
    action_group = parser.add_mutually_exclusive_group(required=True)
    action_group.add_argument("--query", help="Ask AI a question")
    action_group.add_argument("--list_apps", action="store_true", help="List all available apps")
    action_group.add_argument("--connect_app", help="Connect to an OAuth app")
    action_group.add_argument("--list_connected_apps", action="store_true", help="List connected apps")
    
    # Required parameters
    parser.add_argument("--entity_id", help="Your unique entity ID (like email)")
    parser.add_argument("--composio_api_key", help="Your Composio API key")
    parser.add_argument("--openai_key", help="Your OpenAI API key (required for queries)")
    
    args = parser.parse_args()
    
    print_banner()

    model = OpenAIChat("o4-mini-2025-04-16", api_key=args.openai_key)
    agent_selection_model = OpenAIChat("gpt-4.1-nano-2025-04-14", api_key=args.openai_key)
    
    # UserId for the memories
    user_id = args.entity_id
    # Database file for memory and storage
    db_file = "tmp/agent.db"    

    # Initialize memory.v2
    memory = Memory(
        # Use any model for creating memories
        model=OpenAIChat(id="o4-mini-2025-04-16", api_key=args.openai_key),
        db=SqliteMemoryDb(table_name="user_memories", db_file=db_file),
    )

    # Initialize storage
    storage = SqliteStorage(table_name="agent_sessions", db_file=db_file)

    # # Initialize Agent
    # memory_agent = Agent(
    #     model=OpenAIChat(id="o4-mini-2025-04-16", api_key=args.openai_key),
    #     # Store memories in a database
    #     memory=memory,
    #     # Give the Agent the ability to update memories
    #     enable_agentic_memory=True,
    #     # OR - Run the MemoryManager after each response
    #     enable_user_memories=True,
    #     # Store the chat history in the database
    #     storage=storage,
    #     # Add the chat history to the messages
    #     add_history_to_messages=True,
    #     # Number of history runs
    #     num_history_runs=3,
    #     markdown=True,
    # )

    # memory.clear()


    # Validate required parameters based on action
    if args.query:
        if not all([args.entity_id, args.composio_api_key, args.openai_key]):
            print("❌ For queries, you need: --entity_id, --composio_api_key, and --openai_key")
            sys.exit(1)

        asyncio.run(process_query(args.query, args.entity_id, args.openai_key, args.composio_api_key, model, agent_selection_model, memory, storage))

    elif args.list_apps:
        if not args.composio_api_key:
            print("❌ --composio_api_key is required")
            sys.exit(1)
        
        list_all_apps(args.composio_api_key)
    
    elif args.connect_app:
        if not all([args.entity_id, args.composio_api_key]):
            print("❌ For app connection, you need: --entity_id and --composio_api_key")
            sys.exit(1)
        
        connect_app(args.connect_app, args.entity_id, args.composio_api_key)
    
    elif args.list_connected_apps:
        if not all([args.entity_id, args.composio_api_key]):
            print("❌ For listing connections, you need: --entity_id and --composio_api_key")
            sys.exit(1)
        
        list_connected_apps(args.entity_id, args.composio_api_key)


if __name__ == "__main__":
    main()