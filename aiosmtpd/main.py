__all__ = [
    'main',
    ]


from aiosmtpd.smtp import DATA_SIZE_DEFAULT
from argparse import ArgumentParser


DEFAULT_HOST = 'localhost'
DEFAULT_PORT = 8025


def parseargs():
    parser = ArgumentParser(
        description='An RFC 5321 SMTP server with extensions',
        epilog="""Additional arguments can be given on the command line which
                  are passed to the handler CLASS.""")
    parser.add_argument(
        '-n', '--nosetuid',
        help="""This program generally tries to setuid `nobody', unless this
                flag is set.  The setuid call will fail if this program is not
                run as root (in which case, use this flag).""")
    parser.add_argument(
        '-c', '--class',
        default='aiosmtpd.events.Debugging',
        help="""Use the given class, as a Python dotted import path, as the
                handler class for SMTP events.  This class can process
                received messages and do other actions during the SMTP
                dialog.  Uses a debugging handler by default.""")
    parser.add_argument(
        '-s', '--size',
        type=int,
        help="""Restrict the total size of the incoming message to
                SIZE number of bytes via the RFC 1870 SIZE extension.
                Defaults to {} bytes.""".format(DATA_SIZE_DEFAULT))
    parser.add_argument(
        '-u', '--smtputf8',
        default=False, action='store_true',
        help="""Enable the SMTPUTF8 extension and behave as an RFC 6531
                SMTP proxy.""")
    parser.add_argument(
        '-d', '--debug',
        default=0, action='count',
        help="""Increase debugging output.""")
    parser.add_argument(
        'hostport', metavar='HOST:PORT',
        nargs='?', default=None,
        help="""Optional host and port to listen on.  If the PORT part is not
                given, then port {port} is used.  If only :PORT is given,
                then {host} is used for the hostname.  If neither are given,
                {host}:{port} is used.""".format(
                    host=DEFAULT_HOST, port=DEFAULT_PORT))
    args = parseargs()
    return args


if __name__ == '__main__':
    options = parseargs()
    # Become nobody
    classname = options.classname
    if "." in classname:
        lastdot = classname.rfind(".")
        mod = __import__(classname[:lastdot], globals(), locals(), [""])
        classname = classname[lastdot+1:]
    else:
        import __main__ as mod
    class_ = getattr(mod, classname)
    proxy = class_((options.localhost, options.localport),
                   (options.remotehost, options.remoteport),
                   options.size_limit, enable_SMTPUTF8=options.enable_SMTPUTF8)
    if options.setuid:
        try:
            import pwd
        except ImportError:
            print('Cannot import module "pwd"; try running with -n option.', file=sys.stderr)
            sys.exit(1)
        nobody = pwd.getpwnam('nobody')[2]
        try:
            os.setuid(nobody)
        except PermissionError:
            print('Cannot setuid "nobody"; try running with -n option.', file=sys.stderr)
            sys.exit(1)

    def handler():
        return class_.channel_class(proxy, 'FIXME', 'FIXME', options.size_limit,
                                    None, options.enable_SMTPUTF8)

    import logging
    logging.basicConfig(level=logging.DEBUG)
    loop = asyncio.get_event_loop()
    server = loop.run_until_complete(
        loop.create_server(handler, options.localhost, options.localport))
    loop.add_signal_handler(signal.SIGINT, loop.stop)
    loop.run_forever()
    server.close()
    # XXX This cleanup is probably incomplete.
    loop.run_until_complete(server.wait_closed())
    loop.close()
