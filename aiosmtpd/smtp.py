import ssl
import socket
import asyncio
import logging
import binascii
import collections
import asyncio.sslproto as sslproto

from base64 import b64decode
from email._header_value_parser import get_addr_spec, get_angle_addr
from email.errors import HeaderParseError
from public import public
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    List,
    NamedTuple,
    Optional,
    Union,
)
from warnings import warn


__version__ = '1.2.2'
__ident__ = 'Python SMTP {}'.format(__version__)
log = logging.getLogger('mail.log')


DATA_SIZE_DEFAULT = 33554432
EMPTYBYTES = b''
NEWLINE = '\n'


class _Missing:
    pass


MISSING = _Missing()


AuthMechanismType = Callable[["SMTP", List[str]], Awaitable[Any]]
_TriStateType = Union[None, _Missing, bytes]


class _AuthMechAttr(NamedTuple):
    method: AuthMechanismType
    is_builtin: bool


@public
class Session:
    def __init__(self, loop):
        self.peer = None
        self.ssl = None
        self.host_name = None
        self.extended_smtp = False
        self.loop = loop
        self.login_data = None


@public
class Envelope:
    def __init__(self):
        self.mail_from = None
        self.mail_options = []
        self.smtp_utf8 = False
        self.content: Union[None, bytes, str] = None
        self.original_content = None
        self.rcpt_tos = []
        self.rcpt_options = []


# This is here to enable debugging output when the -E option is given to the
# unit test suite.  In that case, this function is mocked to set the debug
# level on the loop (as if PYTHONASYNCIODEBUG=1 were set).
def make_loop():
    return asyncio.get_event_loop()


def syntax(text, extended=None, when=None):
    def decorator(f):
        f.__smtp_syntax__ = text
        f.__smtp_syntax_extended__ = extended
        f.__smtp_syntax_when__ = when
        return f
    return decorator


def login_always_fail(mechanism, login, password):
    return False


@public
class SMTP(asyncio.StreamReaderProtocol):
    command_size_limit = 512
    command_size_limits = collections.defaultdict(
        lambda x=command_size_limit: x)

    def __init__(self, handler,
                 *,
                 data_size_limit=DATA_SIZE_DEFAULT,
                 enable_SMTPUTF8=False,
                 decode_data=False,
                 hostname=None,
                 ident=None,
                 tls_context=None,
                 require_starttls=False,
                 timeout=300,
                 auth_required=False,
                 auth_require_tls=True,
                 auth_exclude_mechanism: Optional[Iterable[str]] = None,
                 auth_callback: Callable[[str, bytes, bytes], bool] = None,
                 loop=None):
        self.__ident__ = ident or __ident__
        self.loop = loop if loop else make_loop()
        super().__init__(
            asyncio.StreamReader(loop=self.loop),
            client_connected_cb=self._client_connected_cb,
            loop=self.loop)
        self.event_handler = handler
        self.data_size_limit = data_size_limit
        self.enable_SMTPUTF8 = enable_SMTPUTF8
        self._decode_data = decode_data
        self.command_size_limits.clear()
        if hostname:
            self.hostname = hostname
        else:
            self.hostname = socket.getfqdn()
        self.tls_context = tls_context
        if tls_context:
            # Through rfc3207 part 4.1 certificate checking is part of SMTP
            # protocol, not SSL layer.
            self.tls_context.check_hostname = False
            self.tls_context.verify_mode = ssl.CERT_NONE
        self.require_starttls = tls_context and require_starttls
        self._timeout_duration = timeout
        self._timeout_handle = None
        self._tls_handshake_okay = True
        self._tls_protocol = None
        self._original_transport = None
        self.session = None
        self.envelope = None
        self.transport = None
        self._handler_coroutine = None
        if not auth_require_tls and auth_required:
            warn("Requiring AUTH while not requiring TLS "
                 "can lead to security vulnerabilities!")
        self._auth_require_tls = auth_require_tls
        self._auth_callback = auth_callback or login_always_fail
        self._auth_required = auth_required
        self.authenticated = False
        # Get hooks & methods to significantly speedup getattr's
        self._auth_methods: Dict[str, _AuthMechAttr] = {
            m.replace("auth_", ""): _AuthMechAttr(getattr(h, m), h is self)
            for h in (self, handler)
            for m in dir(h)
            if m.startswith("auth_")
        }
        for m in (auth_exclude_mechanism or []):
            self._auth_methods.pop(m, None)
        msg = "Available AUTH mechanisms:"
        for m, impl in sorted(
                self._auth_methods.items()):  # type: str, _AuthMechAttr
            msg += f" {m}{'(builtin)' if impl.is_builtin else ''}"
        log.info(msg)
        self._handle_hooks: Dict[str, Callable] = {
            m.replace("handle_", ""): getattr(handler, m)
            for m in dir(handler)
            if m.startswith("handle_")
        }
        self._smtp_methods: Dict[str, Any] = {
            m.replace("smtp_", ""): getattr(self, m)
            for m in dir(self)
            if m.startswith("smtp_")
        }

    def _create_session(self):
        return Session(self.loop)

    def _create_envelope(self):
        return Envelope()

    async def _call_handler_hook(self, command, *args):
        hook = self._handle_hooks.get(command)
        if hook is None:
            return MISSING
        status = await hook(self, self.session, self.envelope, *args)
        return status

    @property
    def max_command_size_limit(self):
        try:
            return max(self.command_size_limits.values())
        except ValueError:
            return self.command_size_limit

    def connection_made(self, transport):
        # Reset state due to rfc3207 part 4.2.
        self._set_rset_state()
        self.session = self._create_session()
        self.session.peer = transport.get_extra_info('peername')
        self._reset_timeout()
        seen_starttls = (self._original_transport is not None)
        if self.transport is not None and seen_starttls:
            # It is STARTTLS connection over normal connection.
            self._reader._transport = transport
            self._writer._transport = transport
            self.transport = transport
            # Do SSL certificate checking as rfc3207 part 4.1 says.  Why is
            # _extra a protected attribute?
            self.session.ssl = self._tls_protocol._extra
            hook = self._handle_hooks.get("STARTTLS")
            if hook is None:
                self._tls_handshake_okay = True
            else:
                self._tls_handshake_okay = hook(
                    self, self.session, self.envelope)
        else:
            super().connection_made(transport)
            self.transport = transport
            log.info('Peer: %r', self.session.peer)
            # Process the client's requests.
            self._handler_coroutine = self.loop.create_task(
                self._handle_client())

    def connection_lost(self, error):
        log.info('%r connection lost', self.session.peer)
        self._timeout_handle.cancel()
        # If STARTTLS was issued, then our transport is the SSL protocol
        # transport, and we need to close the original transport explicitly,
        # otherwise an unexpected eof_received() will be called *after* the
        # connection_lost().  At that point the stream reader will already be
        # destroyed and we'll get a traceback in super().eof_received() below.
        if self._original_transport is not None:
            self._original_transport.close()
        super().connection_lost(error)
        self._handler_coroutine.cancel()
        self.transport = None

    def eof_received(self):
        log.info('%r EOF received', self.session.peer)
        self._handler_coroutine.cancel()
        if self.session.ssl is not None:            # pragma: nomswin
            # If STARTTLS was issued, return False, because True has no effect
            # on an SSL transport and raises a warning. Our superclass has no
            # way of knowing we switched to SSL so it might return True.
            #
            # This entire method seems not to be called during any of the
            # starttls tests on Windows.  I don't really know why, but it
            # causes these lines to fail coverage, hence the `nomswin` pragma
            # above.
            return False
        return super().eof_received()

    def _reset_timeout(self):
        if self._timeout_handle is not None:
            self._timeout_handle.cancel()

        self._timeout_handle = self.loop.call_later(
            self._timeout_duration, self._timeout_cb)

    def _timeout_cb(self):
        log.info('%r connection timeout', self.session.peer)

        # Calling close() on the transport will trigger connection_lost(),
        # which gracefully closes the SSL transport if required and cleans
        # up state.
        self.transport.close()

    def _client_connected_cb(self, reader, writer):
        # This is redundant since we subclass StreamReaderProtocol, but I like
        # the shorter names.
        self._reader = reader
        self._writer = writer

    def _set_post_data_state(self):
        """Reset state variables to their post-DATA state."""
        self.envelope = self._create_envelope()

    def _set_rset_state(self):
        """Reset all state variables except the greeting."""
        self._set_post_data_state()

    async def push(self, status):
        response = bytes(
            status + '\r\n', 'utf-8' if self.enable_SMTPUTF8 else 'ascii')
        self._writer.write(response)
        log.debug(response)
        await self._writer.drain()

    async def handle_exception(self, error):
        if hasattr(self.event_handler, 'handle_exception'):
            status = await self.event_handler.handle_exception(error)
            return status
        else:
            log.exception('SMTP session exception')
            status = '500 Error: ({}) {}'.format(
                error.__class__.__name__, str(error))
            return status

    async def _handle_client(self):
        log.info('%r handling connection', self.session.peer)
        await self.push('220 {} {}'.format(self.hostname, self.__ident__))
        while self.transport is not None:   # pragma: nobranch
            # XXX Put the line limit stuff into the StreamReader?
            try:
                line: bytes = await self._reader.readline()
                log.debug('_handle_client readline: %s', line)
                # XXX this rstrip may not completely preserve old behavior.
                line = line.rstrip(b'\r\n')
                log.info('%r Data: %s', self.session.peer, line)
                if not line:
                    await self.push('500 Error: bad syntax')
                    continue
                command, _, arg = line.partition(b" ")
                # Decode to string only the command name part, which must be
                # ASCII as per RFC.  If there is an argument, it is decoded to
                # UTF-8/surrogateescape so that non-UTF-8 data can be
                # re-encoded back to the original bytes when the SMTP command
                # is handled.
                try:
                    command = command.upper().decode(encoding='ascii')
                except UnicodeDecodeError:
                    await self.push('500 Error: bad syntax')
                    continue
                if not arg:
                    arg = None
                else:
                    arg = arg.strip()
                    # Remote SMTP servers can send us UTF-8 content despite
                    # whether they've declared to do so or not.  Some old
                    # servers can send 8-bit data.  Use surrogateescape so
                    # that the fidelity of the decoding is preserved, and the
                    # original bytes can be retrieved.
                    if self.enable_SMTPUTF8:
                        arg = str(
                            arg, encoding='utf-8', errors='surrogateescape')
                    else:
                        try:
                            arg = str(arg, encoding='ascii', errors='strict')
                        except UnicodeDecodeError:
                            # This happens if enable_SMTPUTF8 is false, meaning
                            # that the server explicitly does not want to
                            # accept non-ASCII, but the client ignores that and
                            # sends non-ASCII anyway.
                            await self.push('500 Error: strict ASCII mode')
                            # Should we await self.handle_exception()?
                            continue
                max_sz = (self.command_size_limits[command]
                          if self.session.extended_smtp
                          else self.command_size_limit)
                if len(line) > max_sz:
                    await self.push('500 Error: line too long')
                    continue
                if not self._tls_handshake_okay and command != 'QUIT':
                    await self.push(
                        '554 Command refused due to lack of security')
                    continue
                if (self.require_starttls
                        and not self._tls_protocol
                        and command not in ['EHLO', 'STARTTLS', 'QUIT']):
                    # RFC3207 part 4
                    await self.push('530 Must issue a STARTTLS command first')
                    continue
                method = self._smtp_methods.get(command)
                if method is None:
                    await self.push(
                        '500 Error: command "%s" not recognized' % command)
                    continue

                # Received a valid command, reset the timer.
                self._reset_timeout()
                await method(arg)
            except asyncio.CancelledError:
                # The connection got reset during the DATA command.
                # XXX If handler method raises ConnectionResetError, we should
                # verify that it was actually self._reader that was reset.
                log.info('Connection lost during _handle_client()')
                self._writer.close()
                raise
            except Exception as error:
                try:
                    status = await self.handle_exception(error)
                    await self.push(status)
                except Exception as error:
                    try:
                        log.exception('Exception in handle_exception()')
                        status = '500 Error: ({}) {}'.format(
                            error.__class__.__name__, str(error))
                    except Exception:
                        status = '500 Error: Cannot describe error'
                    await self.push(status)

    async def check_helo_needed(self, helo: str = "HELO") -> bool:
        """
        Check if HELO/EHLO is needed.

        :param helo: The actual string of HELO/EHLO
        :return: True if HELO/EHLO is needed
        """
        if not self.session.host_name:
            await self.push(f'503 Error: send {helo} first')
            return True
        return False

    async def check_auth_needed(self, caller_method: str) -> bool:
        """
        Check if AUTH is needed.

        :param caller_method: The SMTP method needing a check (for logging)
        :return: True if AUTH is needed
        """
        if self._auth_required and not self.authenticated:
            log.info(f'{caller_method}: Authentication required')
            await self.push('530 5.7.0 Authentication required')
            return True
        return False

    # SMTP and ESMTP commands
    @syntax('HELO hostname')
    async def smtp_HELO(self, hostname):
        if not hostname:
            await self.push('501 Syntax: HELO hostname')
            return
        self._set_rset_state()
        self.session.extended_smtp = False
        status = await self._call_handler_hook('HELO', hostname)
        if status is MISSING:
            self.session.host_name = hostname
            status = '250 {}'.format(self.hostname)
        await self.push(status)

    @syntax('EHLO hostname')
    async def smtp_EHLO(self, hostname):
        if not hostname:
            await self.push('501 Syntax: EHLO hostname')
            return
        self._set_rset_state()
        self.session.extended_smtp = True
        await self.push('250-%s' % self.hostname)
        if self.data_size_limit:
            await self.push('250-SIZE %s' % self.data_size_limit)
            self.command_size_limits['MAIL'] += 26
        if not self._decode_data:
            await self.push('250-8BITMIME')
        if self.enable_SMTPUTF8:
            await self.push('250-SMTPUTF8')
            self.command_size_limits['MAIL'] += 10
        if self.tls_context and not self._tls_protocol:
            await self.push('250-STARTTLS')
        if hasattr(self, 'ehlo_hook'):
            warn('Use handler.handle_EHLO() instead of .ehlo_hook()',
                 DeprecationWarning)
            await self.ehlo_hook()
        if not self._auth_require_tls or self._tls_protocol:
            await self.push(
                "250-AUTH " + " ".join(sorted(self._auth_methods.keys()))
            )
        status = await self._call_handler_hook('EHLO', hostname)
        if status is MISSING:
            self.session.host_name = hostname
            status = '250 HELP'
        await self.push(status)

    @syntax('NOOP [ignored]')
    async def smtp_NOOP(self, arg):
        status = await self._call_handler_hook('NOOP', arg)
        await self.push('250 OK' if status is MISSING else status)

    @syntax('QUIT')
    async def smtp_QUIT(self, arg):
        if arg:
            await self.push('501 Syntax: QUIT')
        else:
            status = await self._call_handler_hook('QUIT')
            await self.push('221 Bye' if status is MISSING else status)
            self._handler_coroutine.cancel()
            self.transport.close()

    @syntax('STARTTLS', when='tls_context')
    async def smtp_STARTTLS(self, arg):
        log.info('%r STARTTLS', self.session.peer)
        if arg:
            await self.push('501 Syntax: STARTTLS')
            return
        if not self.tls_context:
            await self.push('454 TLS not available')
            return
        await self.push('220 Ready to start TLS')
        # Create SSL layer.
        # noinspection PyTypeChecker
        self._tls_protocol = sslproto.SSLProtocol(
            self.loop,
            self,
            self.tls_context,
            None,
            server_side=True)
        # Reconfigure transport layer.  Keep a reference to the original
        # transport so that we can close it explicitly when the connection is
        # lost.  XXX BaseTransport.set_protocol() was added in Python 3.5.3 :(
        self._original_transport = self.transport
        self._original_transport._protocol = self._tls_protocol
        # Reconfigure the protocol layer.  Why is the app transport a protected
        # property, if it MUST be used externally?
        self.transport = self._tls_protocol._app_transport
        self._tls_protocol.connection_made(self._original_transport)

    @syntax("AUTH <mechanism>")
    async def smtp_AUTH(self, arg: str) -> None:
        if await self.check_helo_needed("EHLO"):
            return
        elif not self.session.extended_smtp:
            await self.push("500 Error: command 'AUTH' not recognized")
        elif self._auth_require_tls and not self._tls_protocol:
            await self.push("538 5.7.11 Encryption required for requested "
                            "authentication mechanism")
        elif self.authenticated:
            await self.push('503 Already authenticated')
        elif not arg:
            await self.push('501 Not enough value')
        else:
            args = arg.split()
            if len(args) > 2:
                await self.push('501 Too many values')
                return

            mechanism = args[0]
            if mechanism not in self._auth_methods:
                await self.push('504 5.5.4 Unrecognized authentication type')
                return

            status = await self._call_handler_hook('AUTH', args)
            if status is MISSING:
                method = self._auth_methods[mechanism]
                if method.is_builtin:
                    log.debug(f"Using builtin auth_ hook for {mechanism}")
                else:
                    log.debug(f"Using handler auth_ hook for {mechanism}")
                # Pass 'self' to method so external methods can leverage this
                # class's helper methods such as push()
                login_data = await method.method(self, args)
                log.debug(f"auth_{mechanism} returned {login_data}")
                if login_data is None:
                    # None means there's an error already handled by method and
                    # we don't need to do anything more
                    return
                elif login_data is MISSING:
                    # MISSING means no error in AUTH process, but credentials
                    # is rejected / not valid
                    status = '535 5.7.8 Authentication credentials invalid'
                else:
                    self.authenticated = True
                    self.session.login_data = login_data
                    status = '235 2.7.0 Authentication successful'
            if status is not None:  # pragma: no branch
                await self.push(status)

    async def _auth_interact(self, server_message) -> _TriStateType:
        blob: bytes
        await self.push(server_message)
        line = await self._reader.readline()
        blob = line.strip()
        # '=' and '*' handling are in accordance with RFC4954
        if blob == b"=":
            log.debug("User responded with '='")
            return None
        if blob == b"*":
            log.warning("User requested abort with '*'")
            await self.push("501 Auth aborted")
            return MISSING
        try:
            decoded_blob = b64decode(blob, validate=True)
        except binascii.Error:
            await self.push("501 5.5.2 Can't decode base64")
            return MISSING
        return decoded_blob

    # IMPORTANT NOTES FOR THE auth_* METHODS
    #
    # 1. For internal methods, due to how they are called, we must ignore
    #    the first arg
    # 2. All auth_* methods can return one of three values:
    #    - None: An error happened and handled;
    #            smtp_AUTH should do nothing more
    #    - MISSING: No error during SMTP AUTH process, but authentication
    #               failed
    #    - [Any]: Authentication succeeded and this is the 'identity' of
    #             the SMTP user
    #      - 'identity' is not always username, depending on the auth mecha-
    #        nism. Might be a session key, a one-time user ID, or any kind of
    #        object, actually.
    #      - If the client provides "=" for username during interaction, the
    #        method MUST return b"" (empty bytes)
    # 3. Auth credentials checking is performed in the auth_* methods because
    #    more advanced auth mechanism might not return login+password pair
    #    (see #2 above)

    async def auth_PLAIN(self, _, args: List[str]):
        loginpassword: _TriStateType
        if len(args) == 1:
            # Trailing space is MANDATORY
            # See https://tools.ietf.org/html/rfc4954#page-4
            loginpassword = await self._auth_interact("334 ")
            if loginpassword is MISSING:
                return
        else:
            blob = args[1].encode()
            if blob == b"=":
                loginpassword = None
            else:
                try:
                    loginpassword = b64decode(blob, validate=True)
                except Exception:
                    await self.push("501 5.5.2 Can't decode base64")
                    return
        if loginpassword is None:
            login = password = None
        else:
            try:
                _, login, password = loginpassword.split(b"\x00")
            except ValueError:  # not enough args
                await self.push("501 5.5.2 Can't split auth value")
                return
        if self._auth_callback("PLAIN", login, password):
            if login is None:
                login = EMPTYBYTES
            return login
        else:
            return MISSING

    async def auth_LOGIN(self, _, args: List[str]):
        login: _TriStateType
        # 'User Name\x00'
        login = await self._auth_interact("334 VXNlciBOYW1lAA==")
        if login is MISSING:
            return

        password: _TriStateType
        # 'Password\x00'
        password = await self._auth_interact("334 UGFzc3dvcmQA")
        if password is MISSING:
            return

        if self._auth_callback("LOGIN", login, password):
            if login is None:  # pragma: no branch
                login = EMPTYBYTES
            return login
        else:
            return MISSING

    def _strip_command_keyword(self, keyword, arg):
        keylen = len(keyword)
        if arg[:keylen].upper() == keyword:
            return arg[keylen:].strip()
        return None

    def _getaddr(self, arg):
        if not arg:
            return '', ''
        if arg.lstrip().startswith('<'):
            address, rest = get_angle_addr(arg)
        else:
            address, rest = get_addr_spec(arg)
        try:
            address = address.addr_spec
        except IndexError:
            # Workaround http://bugs.python.org/issue27931
            address = None
        return address, rest

    def _getparams(self, params):
        # Return params as dictionary. Return None if not all parameters
        # appear to be syntactically valid according to RFC 1869.
        result = {}
        for param in params:
            param, eq, value = param.partition('=')
            if not param.isalnum() or eq and not value:
                return None
            result[param] = value if eq else True
        return result

    def _syntax_available(self, method):
        if not hasattr(method, '__smtp_syntax__'):
            return False
        if method.__smtp_syntax_when__:
            return bool(getattr(self, method.__smtp_syntax_when__))
        return True

    @syntax('HELP [command]')
    async def smtp_HELP(self, arg: str) -> None:
        if await self.check_auth_needed("HELP"):
            return
        code = 250
        if arg:
            method = self._smtp_methods.get(arg.upper())
            if method and self._syntax_available(method):
                help_str = method.__smtp_syntax__
                if (self.session.extended_smtp
                        and method.__smtp_syntax_extended__):
                    help_str += method.__smtp_syntax_extended__
                await self.push('250 Syntax: ' + help_str)
                return
            code = 501
        commands = []
        for name, method in self._smtp_methods.items():
            if self._syntax_available(method):
                commands.append(name)
        commands.sort()
        await self.push(
            '{} Supported commands: {}'.format(code, ' '.join(commands)))

    @syntax('VRFY <address>')
    async def smtp_VRFY(self, arg: str) -> None:
        if await self.check_auth_needed("VRFY"):
            return
        if arg:
            try:
                address, params = self._getaddr(arg)
            except HeaderParseError:
                address = None
            if address is None:
                await self.push('502 Could not VRFY %s' % arg)
            else:
                status = await self._call_handler_hook('VRFY', address)
                await self.push(
                    '252 Cannot VRFY user, but will accept message '
                    'and attempt delivery'
                    if status is MISSING else status)
        else:
            await self.push('501 Syntax: VRFY <address>')

    @syntax('MAIL FROM: <address>', extended=' [SP <mail-parameters>]')
    async def smtp_MAIL(self, arg: str) -> None:
        if await self.check_helo_needed():
            return
        if await self.check_auth_needed("MAIL"):
            return
        log.debug('===> MAIL %s', arg)
        syntaxerr = '501 Syntax: MAIL FROM: <address>'
        if self.session.extended_smtp:
            syntaxerr += ' [SP <mail-parameters>]'
        if arg is None:
            await self.push(syntaxerr)
            return
        arg = self._strip_command_keyword('FROM:', arg)
        if arg is None:
            await self.push(syntaxerr)
            return
        address, params = self._getaddr(arg)
        if address is None:
            await self.push(syntaxerr)
            return
        if not self.session.extended_smtp and params:
            await self.push(syntaxerr)
            return
        if self.envelope.mail_from:
            await self.push('503 Error: nested MAIL command')
            return
        mail_options = params.upper().split()
        params = self._getparams(mail_options)
        if params is None:
            await self.push(syntaxerr)
            return
        if not self._decode_data:
            body = params.pop('BODY', '7BIT')
            if body not in ['7BIT', '8BITMIME']:
                await self.push(
                    '501 Error: BODY can only be one of 7BIT, 8BITMIME')
                return
        smtputf8 = params.pop('SMTPUTF8', False)
        if not isinstance(smtputf8, bool):
            await self.push('501 Error: SMTPUTF8 takes no arguments')
            return
        if smtputf8 and not self.enable_SMTPUTF8:
            await self.push('501 Error: SMTPUTF8 disabled')
            return
        self.envelope.smtp_utf8 = smtputf8
        size = params.pop('SIZE', None)
        if size:
            if isinstance(size, bool) or not size.isdigit():
                await self.push(syntaxerr)
                return
            elif self.data_size_limit and int(size) > self.data_size_limit:
                await self.push(
                    '552 Error: message size exceeds fixed maximum message '
                    'size')
                return
        if len(params) > 0:
            await self.push(
                '555 MAIL FROM parameters not recognized or not implemented')
            return
        status = await self._call_handler_hook('MAIL', address, mail_options)
        if status is MISSING:
            self.envelope.mail_from = address
            self.envelope.mail_options.extend(mail_options)
            status = '250 OK'
        log.info('%r sender: %s', self.session.peer, address)
        await self.push(status)

    @syntax('RCPT TO: <address>', extended=' [SP <mail-parameters>]')
    async def smtp_RCPT(self, arg: str) -> None:
        if await self.check_helo_needed():
            return
        if await self.check_auth_needed("RCPT"):
            return
        log.debug('===> RCPT %s', arg)
        if not self.envelope.mail_from:
            await self.push('503 Error: need MAIL command')
            return
        syntaxerr = '501 Syntax: RCPT TO: <address>'
        if self.session.extended_smtp:
            syntaxerr += ' [SP <mail-parameters>]'
        if arg is None:
            await self.push(syntaxerr)
            return
        arg = self._strip_command_keyword('TO:', arg)
        if arg is None:
            await self.push(syntaxerr)
            return
        address, params = self._getaddr(arg)
        if not address:
            await self.push(syntaxerr)
            return
        if not self.session.extended_smtp and params:
            await self.push(syntaxerr)
            return
        rcpt_options = params.upper().split()
        params = self._getparams(rcpt_options)
        if params is None:
            await self.push(syntaxerr)
            return
        # XXX currently there are no options we recognize.
        if len(params) > 0:
            await self.push(
                '555 RCPT TO parameters not recognized or not implemented')
            return
        status = await self._call_handler_hook('RCPT', address, rcpt_options)
        if status is MISSING:
            self.envelope.rcpt_tos.append(address)
            self.envelope.rcpt_options.extend(rcpt_options)
            status = '250 OK'
        log.info('%r recip: %s', self.session.peer, address)
        await self.push(status)

    @syntax('RSET')
    async def smtp_RSET(self, arg):
        if arg:
            await self.push('501 Syntax: RSET')
            return
        self._set_rset_state()
        if hasattr(self, 'rset_hook'):
            warn('Use handler.handle_RSET() instead of .rset_hook()',
                 DeprecationWarning)
            await self.rset_hook()
        status = await self._call_handler_hook('RSET')
        await self.push('250 OK' if status is MISSING else status)

    @syntax('DATA')
    async def smtp_DATA(self, arg: str) -> None:
        if await self.check_helo_needed():
            return
        if await self.check_auth_needed("DATA"):
            return
        if not self.envelope.rcpt_tos:
            await self.push('503 Error: need RCPT command')
            return
        if arg:
            await self.push('501 Syntax: DATA')
            return
        await self.push('354 End data with <CR><LF>.<CR><LF>')
        data = []
        num_bytes = 0
        size_exceeded = False
        while self.transport is not None:           # pragma: nobranch
            try:
                line = await self._reader.readline()
                log.debug('DATA readline: %s', line)
            except asyncio.CancelledError:
                # The connection got reset during the DATA command.
                log.info('Connection lost during DATA')
                self._writer.close()
                raise
            if line == b'.\r\n':
                break
            num_bytes += len(line)
            if (not size_exceeded
                    and self.data_size_limit
                    and num_bytes > self.data_size_limit):
                size_exceeded = True
                await self.push('552 Error: Too much mail data')
            if not size_exceeded:
                data.append(line)
        if size_exceeded:
            self._set_post_data_state()
            return
        # Remove extraneous carriage returns and de-transparency
        # according to RFC 5321, Section 4.5.2.
        for i, text in enumerate(data):
            if text.startswith(b'.'):
                data[i] = text[1:]
        content = original_content = EMPTYBYTES.join(data)
        if self._decode_data:
            if self.enable_SMTPUTF8:
                content = original_content.decode(
                    'utf-8', errors='surrogateescape')
            else:
                try:
                    content = original_content.decode('ascii', errors='strict')
                except UnicodeDecodeError:
                    # This happens if enable_smtputf8 is false, meaning that
                    # the server explicitly does not want to accept non-ascii,
                    # but the client ignores that and sends non-ascii anyway.
                    await self.push('500 Error: strict ASCII mode')
                    return
        self.envelope.content = content
        self.envelope.original_content = original_content
        # Call the new API first if it's implemented.
        if "DATA" in self._handle_hooks:
            status = await self._call_handler_hook('DATA')
        else:
            # Backward compatibility.
            status = MISSING
            if hasattr(self.event_handler, 'process_message'):
                warn('Use handler.handle_DATA() instead of .process_message()',
                     DeprecationWarning)
                args = (self.session.peer, self.envelope.mail_from,
                        self.envelope.rcpt_tos, self.envelope.content)
                if asyncio.iscoroutinefunction(
                        self.event_handler.process_message):
                    status = await self.event_handler.process_message(*args)
                else:
                    status = self.event_handler.process_message(*args)
                # The deprecated API can return None which means, return the
                # default status.  Don't worry about coverage for this case as
                # it's a deprecated API that will go away after 1.0.
                if status is None:                  # pragma: nocover
                    status = MISSING
        self._set_post_data_state()
        await self.push('250 OK' if status is MISSING else status)

    # Commands that have not been implemented.
    async def smtp_EXPN(self, arg):
        await self.push('502 EXPN not implemented')
