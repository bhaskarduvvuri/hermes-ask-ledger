"""A stand-in for Hermes's PluginContext that records register_* calls.

It mirrors only the documented signatures the plugin uses (plugin developer
guide, "Write the registration" / "Register slash commands"), so tests can
drive hooks, tools and commands the way Hermes would.
"""


class FakeCtx:
    def __init__(self, config=None):
        self.tools = {}
        self.hooks = {}
        self.commands = {}
        self.config = dict(config or {})

    def register_tool(self, name, toolset, schema, handler, check_fn=None, requires_env=None,
                      is_async=False, description="", emoji="", override=False):
        assert name not in self.tools, f"tool {name} registered twice"
        assert schema.get("name") == name
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler}

    def register_hook(self, hook_name, callback, *, hold_stream=False):
        self.hooks.setdefault(hook_name, []).append(callback)

    def register_command(self, name, handler, description="", args_hint=""):
        assert name not in self.commands
        self.commands[name] = {"handler": handler, "description": description, "args_hint": args_hint}

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    # Drivers -------------------------------------------------------------
    def fire(self, hook_name, **kwargs):
        return [cb(**kwargs) for cb in self.hooks.get(hook_name, [])]

    def call_tool(self, name, args, **kwargs):
        return self.tools[name]["handler"](args, **kwargs)

    def run_command(self, name, raw="", **kwargs):
        return self.commands[name]["handler"](raw, **kwargs)
