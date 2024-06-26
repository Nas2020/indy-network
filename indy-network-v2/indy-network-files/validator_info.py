#source https://github.com/hyperledger/indy-node/blob/master/scripts/validator-info
import importlib
import argparse
import asyncio
import concurrent.futures
import curses
import json
import os
import sys
import time
import subprocess
from collections import OrderedDict
from glob import glob
import re
import pwd
import pathlib

from indy_common.config_util import getConfig
from indy_common.config_helper import ConfigHelper
from stp_core.common.log import Logger
from stp_core.common.log import getlogger

config = getConfig()

logger = getlogger()  # to make flake8 happy
clients = {}   # task -> (reader, writer)
INDY_USER = "indy"


class BaseUnknown:
    def __init__(self, val):
        self._val = val

    def _str(self):
        return str(self._val)

    def __str__(self):
        return self._str() if not self.is_unknown() else "unknown"

    def is_unknown(self):
        return self._val is None

    @property
    def val(self):
        return self._val

    @val.setter
    def val(self, val):
        self._val = val


class NewEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, BaseUnknown):
            return o.val
        elif isinstance(o, ConnectionStatsOut):
            return o.bindings
        else:
            return super().default(o)


class FloatUnknown(BaseUnknown):
    def _str(self):
        return "{:.2f}".format(self.val)


class TimestampUnknown(BaseUnknown):
    def _str(self):
        return "{}".format(
            time.strftime("%A, %B %{0}d, %Y %{0}I:%M:%S %p %z".format('#' if os.name == 'nt' else '-'),
                          time.localtime(self.val)))


class UptimeUnknown(BaseUnknown):
    def _str(self):
        days, remainder = divmod(self.val, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        parts = []

        for s, v in zip(['day', 'hour', 'minute', 'second'],
                        [days, hours, minutes, seconds]):
            if v or len(parts):
                parts.append("{} {}{}".format(v, s, '' if v == 1 else 's'))

        return ", ".join(parts) if parts else '0 seconds'


class StateUnknown(BaseUnknown):
    def __str__(self):
        return self.val if not self.is_unknown() else 'in unknown state'


class NodesListUnknown(BaseUnknown):
    def __init__(self, val):
        super().__init__({} if val is None else {rn[0]: rn[1] for rn in val})

    def _str(self):
        if self.val:
            return "\n".join(["  {}\t{}".format(pr_n, "({})".format(r_idx) if r_idx is not None else "")
                              for pr_n, r_idx in self.val.items()])
        else:
            return ""

    def __iter__(self):
        return iter(self.val)


class BaseStats(OrderedDict):
    shema = []

    def __init__(self, stats, verbose=False):
        if stats is None:
            logger.debug(
                "{} no stats found".format(type(self).__name__))

        for k, cls in self.shema:
            val = None if stats is None else stats.get(k)
            try:
                if issubclass(cls, BaseStats):
                    self[k] = cls(val, verbose=verbose)
                else:
                    self[k] = cls(val)
            except Exception as e:
                logger.warning(
                    "{} Failed to parse attribute '{}': {}".format(
                        type(self).__name__, k, e))
                self[k] = None

        self._verbose = verbose


class ConnectionStatsOut:

    def __init__(self, bindings, verbose):
        self.bindings = bindings
        self._verbose = verbose

    def __str__(self):
        if not self._verbose:
            data = ["{}".format(b['port']) for b in self.bindings]
        else:
            data = [
                "{}{}".format(
                    b['port'],
                    "/{} on {}".format(b['protocol'], b['ip'])
                ) for b in self.bindings
            ]

        data = list(set(data))

        return ", ".join(data)


class BindingStats(BaseUnknown):

    @staticmethod
    def explore_bindings(port):
        ANYADDR_IPV4 = '*'

        def shell_cmd(command):
            res = None
            try:
                ret = subprocess.check_output(
                    command, stderr=subprocess.STDOUT, shell=True)
            except subprocess.CalledProcessError as e:
                logger.warning(
                    "Shell command '{}' failed, "
                    "return code {}, stderr: {}".format(
                        command, e.returncode, e.stderr)
                )
            except Exception as e:
                logger.warning(
                    "Failed to process shell command: '{}', "
                    "unexpected error: {}".format(command, e)
                )
            else:
                logger.debug("command '{}': stdout '{}'".format(command, ret))
                res = ret.decode().strip()

            return res

        if port is None:
            return None

        # TODO
        # 1. need to filter more (e.g. by pid) for such cases as:
        #   - SO_REUSEPORT or SO_REUSEADDR
        #   - tcp + udp
        # 2. procss ipv6 as well
        #
        # parse listening ip using 'ss' tool
        command = "ss -ln4 | sort -u | grep ':{}\s'".format(port)
        ret = shell_cmd(command)

        if ret is None:
            return None

        ips = []
        ips_with_netmasks = {}
        for line in ret.split('\n'):
            try:
                parts = re.compile("\s+").split(line)
                # format:
                # Netid State Recv-Q Send-Q Local Address:Port Peer Address:Port
                protocol, ip = parts[0], parts[4].split(":")[0]
            except Exception as e:
                logger.warning(
                    "Failed to parse protocol, ip from '{}', "
                    "error: {}".format(line, e)
                )
            else:
                if ip == ANYADDR_IPV4:
                    # TODO mask here seems not necessary,
                    # but requested in INDY-715
                    ip = "0.0.0.0/0"
                else:
                    if ip not in ips_with_netmasks:
                        # parse mask using 'ip' tool
                        # TODO more filtering by 'ip' tool itself if possible
                        command = "ip a | grep 'inet {}'".format(ip)
                        ret = shell_cmd(command)

                        try:
                            ip_with_netmask = re.match(
                                "^inet\s([^\s]+)", ret).group(1)
                        except Exception as e:
                            logger.debug(
                                "Failed to parse ip with mask: command {}, "
                                "stdout: {}, error {}".format(command, ret, e))
                            ip = "{}/unknown".format(ip)

                        ips_with_netmasks[ip] = ip_with_netmask

                    ip = ips_with_netmasks[ip]

                ips.append((protocol, ip))

        return list(set(ips))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # change schema: ignoring any data received except port,
        # resolve it ourselves (requested in original task INDY-715)
        # TODO refactor

        bindings = self.explore_bindings(self.val)
        logger.info(
            "Found the following bindings "
            "with port {}: {}".format(self.val, bindings)
        )
        self.val = ConnectionStatsOut([] if bindings is None else [
            dict(port=self.val, protocol=protocol, ip=ip)
            for protocol, ip in bindings
        ], False)


class TransactionsStats(BaseUnknown):
    def __init__(self, val):
        super().__init__({} if val is None else val)

    def _str(self):
        if self.val:
            return "\n".join(["  Total {} Transactions:  {}".format(ledger, cnt) for ledger, cnt in self.val.items()])
        else:
            return ""

    def items(self):
        return dict(self.val).items()

    def __iter__(self):
        return iter(self.val)

    def __getitem__(self, key):
        return self.val[key]

    def __setitem__(self, key, value):
        self.val[key] = value


class AverageStats(BaseStats):
    shema = [
        ("read-transactions", FloatUnknown),
        ("write-transactions", FloatUnknown)
    ]


class MetricsStats(BaseStats):
    shema = [
        ("uptime", UptimeUnknown),
        ("transaction-count", TransactionsStats),
        ("average-per-second", AverageStats)
    ]


class NodeStats(BaseStats):
    shema = [
        ("Name", BaseUnknown),
        ("did", BaseUnknown),
        ("verkey", BaseUnknown),
        ("BLS_key", BaseUnknown),
        ("Node_port", BaseUnknown),
        ("Client_port", BaseUnknown),
        ("Node_ip", BaseUnknown),
        ("Client_ip", BaseUnknown),
        ("Metrics", MetricsStats)
    ]


class PoolStats(BaseStats):
    shema = [
        ("Total_nodes_count", BaseUnknown),
        ("Reachable_nodes", NodesListUnknown),
        ("Reachable_nodes_count", BaseUnknown),
        ("Unreachable_nodes", NodesListUnknown),
        ("Unreachable_nodes_count", BaseUnknown)
    ]


class SoftwareStats(BaseStats):
    shema = [
        ("indy-node", BaseUnknown),
        ("sovrin", BaseUnknown)
    ]

    @staticmethod
    def pkgVersion(pkgName):
        try:
            pkg = importlib.import_module(pkgName)
        except ImportError as e:
            logger.warning("Failed to import {}: {}".format(pkgName, e))
        else:
            try:
                return pkg.__version__
            except AttributeError as e:
                logger.warning(
                    "Failed to get version of {}: {}".format(pkgName, e))
                return None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        pkgMappings = {
            'indy-node': 'indy_node'
        }

        for pkgName, obj in self.items():
            if obj is None or obj.is_unknown():
                self[pkgName] = BaseUnknown(
                    self.pkgVersion(pkgMappings.get(pkgName, pkgName)))


class ValidatorStats(BaseStats):
    shema = [
        ("response-version", BaseUnknown),
        ("timestamp", TimestampUnknown),
        ("Node_info", NodeStats),
        ("state", StateUnknown),
        ("enabled", BaseUnknown),
        ("Pool_info", PoolStats),
        ("software", SoftwareStats)
    ]

    @staticmethod
    def get_process_state():
        ctl = os.getenv('INDY_CONTROL', 'systemctl')
        if ctl == "systemctl":
           return ValidatorStats.get_process_state_via_systemctl()
        elif ctl == "supervisorctl":
           return ValidatorStats.get_process_state_via_supervisorctl()
        else:
           return "Invalid value for INDY_CONTROL environment variable: '%s'" % ctl

    @staticmethod
    def get_process_state_via_systemctl():
        ret = subprocess.check_output(
            'systemctl is-failed indy-node; exit 0',
            stderr=subprocess.STDOUT, shell=True
        )
        ret = ret.decode().strip()
        if ret == 'inactive':
            return 'stopped'
        elif ret == 'active':
            return 'running'
        else:
            logger.info(
                "Non-expected output for indy-node "
                "is-failed state: {}".format(ret)
            )
            return None

    @staticmethod
    def get_process_state_via_supervisorctl():
        ret = subprocess.check_output(
            "supervisorctl status indy-node | awk '{print $2}'; exit 0",
            stderr=subprocess.STDOUT, shell=True
        )
        ret = ret.decode().strip()
        if ret == 'STOPPED':
            return 'stopped'
        elif ret == 'RUNNING':
            return 'running'
        else:
            logger.info(
                "Non-expected output for indy-node "
                "status: {}".format(ret)
            )
            return None

    @staticmethod
    def get_enabled_state():
        ctl = os.getenv('INDY_CONTROL', 'systemctl')
        if ctl == "systemctl":
           return ValidatorStats.get_enabled_state_via_systemctl()
        elif ctl == "supervisorctl":
           return ValidatorStats.get_enabled_state_via_supervisorctl()
        else:
           return "Invalid value for INDY_CONTROL environment variable: '%s'" % ctl

    @staticmethod
    def get_enabled_state_via_systemctl():
        ret = subprocess.check_output(
            'systemctl is-enabled indy-node; exit 0',
            stderr=subprocess.STDOUT, shell=True
        )
        ret = ret.decode().strip()
        if ret in ('enabled', 'static'):
            return True
        elif ret == 'disabled':
            return False
        else:
            logger.info(
                "Non-expected output for indy-node "
                "is-enabled state: {}".format(ret)
            )
            return None

    @staticmethod
    def get_enabled_state_via_supervisorctl():
        ret = subprocess.check_output(
            "supervisorctl status indy-node | awk '{print $2}'; exit 0",
            stderr=subprocess.STDOUT, shell=True
        )
        ret = ret.decode().strip()
        if ret in ('RUNNING', 'BACKOFF', 'STARTING'):
            return True
        elif ret == 'STOPPED':
            return False
        else:
            logger.info(
                "Non-expected output for indy-node "
                "is-enabled state: {}".format(ret)
            )
            return None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # TODO move that to classes too

        if self['state'].is_unknown():
            self['state'] = StateUnknown(self.get_process_state())

        if self['enabled'].is_unknown():
            self['enabled'] = BaseUnknown(self.get_enabled_state())

    def __str__(self):
        # TODO moving parts to other classes seems reasonable but
        # will drop visibility of output
        lines = [
            "Validator {} is {}".format(self['Node_info']['Name'], self['state']),
            "Update time:     {}".format(self['timestamp']),
            "Validator DID:    {}".format(self['Node_info']['did']),
            "Verification Key: {}".format(self['Node_info']['verkey']),
            "BLS Key: {}".format(self['Node_info']['BLS_key']),
            "Node HA:        {}:{}".format(self['Node_info']['Node_ip'], self['Node_info']['Node_port']),
            "Client HA:      {}:{}".format(self['Node_info']['Client_ip'], self['Node_info']['Client_port']),
            "Metrics:",
            "  Uptime: {}".format(self['Node_info']['Metrics']['uptime'])
            ] + [
                str(self['Node_info']['Metrics']['transaction-count'])
        ] + [
            "  Read Transactions/Seconds:  {}".format(
                self['Node_info']['Metrics']['average-per-second']['read-transactions']),
            "  Write Transactions/Seconds: {}".format(
                self['Node_info']['Metrics']['average-per-second']['write-transactions']),
            "Reachable Hosts:   {}/{}".format(
                self['Pool_info']['Reachable_nodes_count'],
                self['Pool_info']['Total_nodes_count'])
        ] + [
            str(self['Pool_info']['Reachable_nodes'])
        ] + [
            "Unreachable Hosts: {}/{}".format(
                self['Pool_info']['Unreachable_nodes_count'],
                self['Pool_info']['Total_nodes_count']
            )
        ] + [
            str(self['Pool_info']['Unreachable_nodes'])
        ] + [
            "Software Versions:"
        ] + [
            "  {}: {}".format(pkgName, self['software'][pkgName])
            for pkgName in self['software'].keys()
        ]

        # skip lines with started with '#' if not verbose
        # or remove '#' otherwise
        # return "\n".join(lines)
        return ("\n".join(
            [l[(1 if l[0] == '#' else 0):]
                for l in lines if self._verbose or (l and l[0] != '#')])
        )


async def handle_client(client_reader, client_writer):
    # give client a chance to respond, timeout after 10 seconds
    while True:
        try:
            data = await client_reader.readline()
        except concurrent.futures.CancelledError:
            logger.warning("task has been cancelled")
            return
        except Exception as e:
            logger.exception("failed to readline: {}".format(e))
            return
        else:
            if data is None:
                logger.warning("Expected data, received None")
                return
            elif not data:
                logger.warning("EOF received, closing connection")
                return
            else:
                logger.debug("Received data: {}".format(data))
                stats = json.loads(data.decode())
                print(json.dumps(stats, indent=2, cls=NewEncoder, sort_keys=True))


def accept_client(client_reader, client_writer):
    logger.info("New Connection")
    task = asyncio.Task(handle_client(client_reader, client_writer))

    clients[task] = (client_reader, client_writer)

    def client_done(task):
        del clients[task]
        client_writer.close()
        logger.info("End Connection")

    task.add_done_callback(client_done)


def nagios(vstats):
    state = '2'
    running = "{}".format(vstats['state'])
    if "running" == running:
        state = '0'

    lines = ["{} {}_Total_{}_Transactions config_transactions={} {} Total {} Transactions".format(
            state,
            vstats['Node_info']['Name'],
            ledger_name.title(),
            count,
            vstats['Node_info']['Name'],
            ledger_name.title())
            for ledger_name, count in vstats['Node_info']['Metrics']['transaction-count'].items()]

    lines += ["{} {}_Read_Transactions_per_second read_transactions_per_second={} {} Read Transactions/Second".format(
            state,vstats['Node_info']['Name'],vstats['Node_info']['Metrics']['average-per-second']['read-transactions'],
            vstats['Node_info']['Name'])
    ] + [
        "{} {}_Write_Transactions_per_second write_transactions_per_second={} {} Write Transactions/Second".format(
            state,vstats['Node_info']['Name'],vstats['Node_info']['Metrics']['average-per-second']['write-transactions'],
            vstats['Node_info']['Name'])
    ] + [
        "{} {}_Number_of_Validators number_of_validators={} {} Number of Validators".format(
            state,vstats['Node_info']['Name'],vstats['Pool_info']['Total_nodes_count'],vstats['Node_info']['Name'])
    ] + [
        "{} {}_Reachable_Validators reachable_validators={} {} Reachable Validators".format(
            state,vstats['Node_info']['Name'],vstats['Pool_info']['Reachable_nodes_count'],vstats['Node_info']['Name'])
    ] + [
        "{} {}_Unreachable_Validators unreachable_validators={} {} Unreachable Validators".format(
            state,vstats['Node_info']['Name'],vstats['Pool_info']['Unreachable_nodes_count'],vstats['Node_info']['Name'])
    ]
    return "\n".join(lines)


def get_stats_from_file(stats, verbose, _json, _nagios):

    logger.debug("Data {}".format(stats))
    vstats = ValidatorStats(stats, verbose)

    if _json:
        return json.dumps(vstats, indent=2, cls=NewEncoder, sort_keys=True)
    if _nagios:
        return nagios(vstats) 

    return vstats


def watch(fpath, verbose, _json):

    def _watch(stdscr):
        stats = None
        while True:
            # Clear screen
            stdscr.clear()

            if stats is not None:
                time.sleep(3)
            stdscr.addstr(
                0, 0, str(get_stats_from_file(fpath, verbose, _json))
            )
            stdscr.refresh()

    try:
        curses.wrapper(_watch)
    except KeyboardInterrupt:
        pass


def format_key(key):
    return "{:15}".format('"{}": '.format(key))


def make_indent(indent):
    return indent * "{:5}".format("")


def format_value(value):
    return " {:10}".format(str(value))


def create_print_tree(stats: dict, indent=0, lines=[]):
    for key, value in sorted(stats.items(), key=lambda x: x[0]):
        if isinstance(value, dict):
            lines.append(make_indent(indent) + format_key(key))
            create_print_tree(value, indent + 1, lines)
        elif isinstance(value, list):
            lines.append(make_indent(indent) + format_key(key))
            for line in value:
                lines.append(make_indent(indent + 1) + format_value(line))
        else:
            if isinstance(value, dict) and not value or \
                isinstance(value, list) and not value:
                value = 'n/a'
            lines.append(make_indent(indent) + format_key(key) + format_value(value))
    return lines


def set_log_owner(log_path):
    def set_own():
        try:
            os.chown(log_path, indy_uid, indy_gid)
        except PermissionError as e:
            print("Cannot set owner of {} file to indy".format(log_path))
            print("The owner of {} must be {}:{}".format(log_path, INDY_USER, INDY_USER))
            sys.exit(1)

    indy_ids = pwd.getpwnam(INDY_USER)
    indy_uid = indy_ids.pw_uid
    indy_gid = indy_ids.pw_gid
    if os.path.exists(log_path):
        f_stat = os.stat(log_path)
        if f_stat.st_uid != indy_uid or f_stat.st_gid != indy_gid:
            set_own()
    else:
        pathlib.Path(log_path).touch()
        set_own()


def remove_log_handlers():
    for hndl in logger.root.handlers:
        logger.root.removeHandler(hndl)

    for hndl in logger.handlers:
        logger.removeHandler(hndl)


def read_json(f_path):
    with open(f_path) as f_stats:
        raw_data = f_stats.read()
        try:
            json_data = json.loads(raw_data)
        except json.JSONDecodeError:
            print("File {} has an invalid json format".format(f_path))
            return {}
        return json_data


def compile_json_ouput(file_paths):
    output_data = {}
    for file_path in file_paths:
        json_data = read_json(file_path)
        if json_data:
            output_data.update(json_data)
    return output_data


def main():
    global logger

    def check_unsigned(s):
        res = None
        try:
            res = int(s)
        except ValueError:
            pass
        if res is None or res <= 0:
            raise argparse.ArgumentTypeError(("{!r} is incorrect, "
                                              "should be int > 0").format(s,))
        else:
            return res

    config_helper = ConfigHelper(config)

    parser = argparse.ArgumentParser(
        description=(
            "Tool to explore and gather statistics about running validator"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Verbose mode (command line)"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Format output as JSON (ignores -v)"
    )
    parser.add_argument(
        "--nagios", action="store_true",
        help="Format output as NAGIOS output (ignores -v)"
    )

    statfile_group = parser.add_argument_group(
        "statfile", "settings for exploring validator stats from stat file"
    )

    statfile_group.add_argument(
        "--basedir", metavar="PATH",
        default=config_helper.node_info_dir,
        help=("Path to stats files")
    )
    # statfile_group.add_argument(
    #     "--watch", action="store_true", help="Watch for stats file updates"
    # )

    # socket group is disabled for now due the feature is unsupported
    # socket_group = parser.add_argument_group(
    #     "socket", "settings for exploring validator stats from socket"
    # )
    #
    # socket_group.add_argument(
    #     "--listen", action="store_true",
    #     help="Listen socket for stats (ignores --statfile)"
    # )
    #
    # socket_group.add_argument(
    #     "-i", "--ip", metavar="IP", default=config.STATS_SERVER_IP,
    #     help="Server IP"
    # )
    # socket_group.add_argument(
    #     "-p", "--port", metavar="PORT", default=config.STATS_SERVER_PORT,
    #     type=check_unsigned, help="Server port"
    # )

    other_group = parser.add_argument_group(
        "other", "other settings"
    )

    other_group.add_argument(
        "--log", metavar="FILE",
        default=os.path.join(
            config_helper.log_base_dir,
            os.path.basename(sys.argv[0] + ".log")
        ),
        help="Path to log file")

    args = parser.parse_args()

    remove_log_handlers()

    if args.log:
        set_log_owner(args.log)

    Logger().enableFileLogging(args.log)

    logger.debug("Cmd line arguments: {}".format(args))

    # is not supported for now
    # if args.listen:
    #     logger.info("Starting server on {}:{} ...".format(
    #       args.ip, args.port))
    #     print("Starting server on {}:{} ...".format(args.ip, args.port))
    #
    #     loop = asyncio.get_event_loop()
    #     coro = asyncio.start_server(accept_client,
    #                                 args.ip, args.port, loop=loop)
    #     server = loop.run_until_complete(coro)
    #
    #     logger.info("Serving on {}:{} ...".format(args.ip, args.port))
    #     print('Serving on {} ...'.format(server.sockets[0].getsockname()))
    #
    #     # Serve requests until Ctrl+C is pressed
    #     try:
    #         loop.run_forever()
    #     except KeyboardInterrupt:
    #         pass
    #
    #     logger.info("Stopping server ...")
    #     print("Stopping server ...")
    #
    #     # Close the server
    #     server.close()
    #     for task in clients.keys():
    #         task.cancel()
    #     loop.run_until_complete(server.wait_closed())
    #     loop.close()
    # else:
    all_paths = glob(os.path.join(args.basedir, "*_info.json"))

    files_by_node = dict()

    for path in all_paths:
        bn = os.path.basename(path)
        if not bn:
            continue
        node_name = bn.split("_", maxsplit=1)[0]
        if "additional" in bn:
            files_by_node.setdefault(node_name, {}).update({"additional": path})
        elif "version" in bn:
            files_by_node.setdefault(node_name, {}).update({"version": path})
        else:
            files_by_node.setdefault(node_name, {}).update({"info": path})
    if not files_by_node:
        print('There are no info files in {}'.format(args.basedir))
        return

    if args.json:
        allf = []
        for n, ff in files_by_node.items():
            allf.extend([v for k, v in ff.items()])
        out_json = compile_json_ouput(allf)
        if out_json:
            print(json.dumps(out_json, sort_keys=True))
            sys.exit(0)

    for node in files_by_node:
        inf_ver = [v for k, v in files_by_node[node].items() if k in ["info", "version"]]
        json_data = compile_json_ouput(inf_ver)
        if json_data:
            if args.verbose:
                    print("{}".format(os.linesep).join(create_print_tree(json_data, lines=[])))
            else:
                print(get_stats_from_file(json_data, args.verbose, args.json, args.nagios))

        print('\n')
    if args.verbose:
        for node in files_by_node:
            file_path = files_by_node[node].get("additional", "")
            if not file_path:
                continue
            json_data = read_json(file_path)
            if json_data:
                print("{}".format(os.linesep).join(create_print_tree(json_data, lines=[])))

    logger.info("Done")


if __name__ == "__main__":
    sys.exit(main())