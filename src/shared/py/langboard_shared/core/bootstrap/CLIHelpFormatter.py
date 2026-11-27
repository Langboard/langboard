from argparse import OPTIONAL, SUPPRESS, ZERO_OR_MORE, Action, HelpFormatter, _StoreAction
from gettext import gettext
from re import findall


class CLIHelpFormatter(HelpFormatter):
    """Custom HelpFormatter to provide desired positional arguments help

    _format_usage and _format_action are copied from the original HelpFormatter and modified to provide desired positional arguments help"""

    def _format_usage(self, usage, actions, groups, prefix):
        if prefix is None:
            prefix = gettext("usage: ")

        prog = "%(prog)s" % dict(prog=self._prog)

        # split optionals from positionals
        usage_only = None
        optionals = []
        positionals = []
        for action in actions:
            if action.option_strings:
                optionals.append(action)
                continue

            if not action.metavar:
                positionals.append(action)
                continue

            if isinstance(action.metavar, dict) and "show_usage_only" in action.metavar:
                usage_only = action
                continue

            for metavar in action.metavar:
                new_action = self._create_fake_action(action, metavar)
                new_action.metavar = None
                positionals.append(new_action)

        if usage_only:
            if usage_only.metavar["positional_name"]:  # type: ignore
                usage_only_text = f"{usage_only.metavar['command_name']} <{usage_only.metavar['positional_name']}>"  # type: ignore
            else:
                usage_only_text = usage_only.metavar["command_name"]  # type: ignore
        else:
            usage_only_text = ""

        # build full usage string
        format = self._format_actions_usage
        positional_usage = "|".join(format(positionals, groups).split(" "))
        action_usage = format(optionals, groups)
        usage = " ".join([s for s in [prog, usage_only_text, positional_usage, action_usage] if s])

        # wrap the usage parts if it's too long
        text_width = self._width - self._current_indent
        if len(prefix) + len(usage) > text_width:
            # break usage into wrappable parts
            part_regexp = r"\(.*?\)+(?=\s|$)|" r"\[.*?\]+(?=\s|$)|" r"\S+"
            opt_usage = format(optionals, groups)
            pos_usage = "|".join(format(positionals, groups).split(" "))
            opt_parts = findall(part_regexp, opt_usage)
            pos_parts = findall(part_regexp, pos_usage)
            assert " ".join(opt_parts) == opt_usage
            assert " ".join(pos_parts).replace("<args>", "").strip() == pos_usage.strip()

            if usage_only:
                pos_parts = [f"<{usage_only.metavar['command_name']}>"]  # type: ignore

            # helper for wrapping lines
            def get_lines(parts, indent, prefix=None):
                lines = []
                line = []
                indent_length = len(indent)
                if prefix is not None:
                    line_len = len(prefix) - 1
                else:
                    line_len = indent_length - 1
                for part in parts:
                    if line_len + 1 + len(part) > text_width and line:
                        lines.append(indent + " ".join(line))
                        line = []
                        line_len = indent_length - 1
                    line.append(part)
                    line_len += len(part) + 1
                if line:
                    lines.append(indent + " ".join(line))
                if prefix is not None:
                    lines[0] = lines[0][indent_length:]
                return lines

            # if prog is short, follow it with optionals or positionals
            if len(prefix) + len(prog) + len(usage_only_text) <= 0.75 * text_width:
                indent = " " * (len(prefix) + len(prog) + len(usage_only_text) + 2)
                if opt_parts:
                    lines = get_lines([prog] + pos_parts + opt_parts, indent, prefix)
                elif pos_parts:
                    lines = get_lines([prog] + pos_parts, indent, prefix)
                else:
                    lines = [prog]

            # if prog is long, put it on its own line
            else:
                indent = " " * len(prefix)
                parts = pos_parts + opt_parts
                lines = get_lines(parts, indent)
                if len(lines) > 1:
                    lines = []
                    lines.extend(get_lines(pos_parts, indent))
                    lines.extend(get_lines(opt_parts, indent))
                lines = [prog] + lines

            # join lines into usage
            usage = "\n".join(lines)

        # prefix with 'usage:'
        return "%s%s\n\n" % (prefix, usage)

    def _format_action(self, action):
        if action.metavar and "show_usage_only" in action.metavar:
            return ""

        # determine the required width and the entry label
        if action.metavar and self._is_fake_action(action) and "arg" in action.metavar:  # type: ignore
            help_position = self._action_max_length + 18
        else:
            help_position = self._action_max_length + 20
        help_width = max(self._width - help_position, 11)
        action_width = help_position - self._current_indent - 2

        fake_action_arg = None
        if action.metavar and self._is_fake_action(action):
            fake_action_arg = action.metavar["arg"]  # type: ignore
            if "choices_description" in action.metavar:  # type: ignore
                choices = action.metavar["choices_description"]  # type: ignore
                if isinstance(choices, list):
                    if not action.help:
                        action.help = ""
                    choices_str = ", ".join([str(choice) for choice in choices])
                    if not choices_str:
                        choices_str = "None"
                    action.help += f" Choices: {choices_str}"
            action.metavar = None

        action_header = self._format_action_invocation(action)

        if fake_action_arg:
            action_header = f"{action_header} {fake_action_arg}"

        # no help; start on same line and add a final newline
        if not action.help:
            tup = self._current_indent, "", action_header
            action_header = "%*s%s\n" % tup

        # short action name; start on the same line and pad two spaces
        elif len(action_header) <= action_width:
            tup = self._current_indent, "", action_width, action_header
            action_header = "%*s%-*s  " % tup
            indent_first = 0

        # long action name; start on the next line
        else:
            tup = self._current_indent, "", action_header
            action_header = "%*s%s\n" % tup
            indent_first = help_position

        # collect the pieces of the action help
        parts = [action_header]

        if action.metavar:
            parts.pop()
            for metavar in action.metavar:
                new_action = self._create_fake_action(action, metavar)
                parts.append(self._format_action(new_action))

        # if there was help for the action, add lines of help text
        elif action.help and action.help.strip():
            help_text = self._expand_help(action)
            if help_text:
                help_lines = self._split_lines(help_text, help_width)
                parts.append("%*s%s\n" % (indent_first, "", help_lines[0]))
                for line in help_lines[1:]:
                    parts.append("%*s%s\n" % (help_position, "", line))

        # or add a newline if the description doesn't end with one
        elif not action_header.endswith("\n"):
            parts.append("\n")

        # if there are any sub-actions, add their help as well
        for subaction in self._iter_indented_subactions(action):
            parts.append(self._format_action(subaction))

        # return a single string
        return self._join_parts(parts)

    def _get_help_string(self, action):
        """
        Add the default value to the option help message.

        ArgumentDefaultsHelpFormatter and BooleanOptionalAction when it isn't
        already present. This code will do that, detecting cornercases to
        prevent duplicates or cases where it wouldn't make sense to the end
        user.
        """
        help = action.help or ""

        if "%(default)" not in help:
            if action.default is not SUPPRESS:
                defaulting_nargs = [OPTIONAL, ZERO_OR_MORE]
                if action.option_strings or action.nargs in defaulting_nargs:
                    help += gettext(" (default: %(default)s)")
        return help

    def _create_fake_action(self, action: Action, action_name: str):
        metadata = action.metavar[action_name]  # type: ignore
        new_action_args = {
            "option_strings": [],
            "dest": action_name,
            "type": metadata["type"],  # type: ignore
            "help": metadata["help"],  # type: ignore
        }

        if metadata["type"] is str:  # type: ignore
            new_action_args["metavar"] = {
                "is_fake": True,
                "arg": f"<{metadata['positional_name']}>",  # type: ignore
            }

            if "choices_description" in metadata:
                new_action_args["metavar"]["choices_description"] = metadata["choices_description"]  # type: ignore

        return _StoreAction(**new_action_args)

    def _is_fake_action(self, action: Action) -> bool:
        return action.metavar and "is_fake" in action.metavar and action.metavar["is_fake"]  # type: ignore

    def _format_action_invocation(self, action):
        if not action.option_strings:
            default = self._get_default_metavar_for_positional(action)
            return ""

        else:
            parts = []

            # if the Optional doesn't take a value, format is:
            #    -s, --long
            if action.nargs == 0:
                parts.extend(action.option_strings)

            # if the Optional takes a value, format is:
            #    -s ARGS, --long ARGS
            else:
                default = self._get_default_metavar_for_optional(action)
                args_string = self._format_args(action, default)
                for option_string in action.option_strings:
                    parts.append("%s %s" % (option_string, args_string))

            return ", ".join(parts)
