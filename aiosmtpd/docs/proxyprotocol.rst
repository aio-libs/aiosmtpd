.. _ProxyProtocol:

========================
 PROXY Protocol Support
========================

When put behind a "proxy" / load balancer,
server programs can no longer "see" the original client's actual IP Address and Port.

This also affects ``aiosmtpd``.

The |HAProxyDevelopers|_ have created a protocol called "PROXY Protocol"
designed to solve this issue.
You can read the reasoning behind this in `their blog`_.

.. _`HAProxyDevelopers`: https://www.haproxy.com/company/about-us/
.. |HAProxyDevelopers| replace:: **HAProxy Developers**
.. _their blog: https://www.haproxy.com/blog/haproxy/proxy-protocol/

This initiative has been accepted and supported by many important software and services
such as `Amazon Web Services`_, `haproxy`_, `nginx`_, `stunnel`_, `varnish`_, and many others.

.. _Amazon Web Services: https://docs.aws.amazon.com/elasticloadbalancing/latest/classic/enable-proxy-protocol.html
.. _haproxy: http://cbonte.github.io/haproxy-dconv/2.3/configuration.html#5.2-send-proxy
.. _nginx: https://nginx.org/en/docs/stream/ngx_stream_proxy_module.html#proxy_protocol
.. _stunnel: https://www.stunnel.org/static/stunnel.html#proxy
.. _varnish: https://info.varnish-software.com/blog/proxy-protocol-original-value-client-identity

``aiosmtpd`` implements the PROXY Protocol as defined in |HAProxy2.3.0|_;
*both* Version 1 and Version 2 are supported.


Activating
==========

To activate ``aiosmtpd``'s PROXY Protocol Support,
you have to set the :attr:`proxy_protocol_timeout` parameter of the SMTP Class
to a positive numeric value (``int`` or ``float``)

The `PROXY Protocol documentation suggests`_ that the timeout should not be less than 3.0 seconds.

.. _PROXY Protocol documentation suggests: https://github.com/haproxy/haproxy/blob/1c0a722a83e7c45456a2b82c15889ab9ab5c4948/doc/proxy-protocol.txt#L172-L174

.. important::

   Once you activate PROXY Protocol support,
   standard (E)SMTP handshake is **no longer available**.

   Clients trying to connect to ``aiosmtpd`` will be REQUIRED
   to send the PROXY Protocol Header
   before they can continue with (E)SMTP transaction.

   This is `as specified`_ in the PROXY Protocol documentation.

.. _as specified: https://github.com/haproxy/haproxy/blob/1c0a722a83e7c45456a2b82c15889ab9ab5c4948/doc/proxy-protocol.txt#L176-L180

.. _HAProxy2.3.0: https://github.com/haproxy/haproxy/blob/v2.3.0/doc/proxy-protocol.txt
.. |HAProxy2.3.0| replace:: **HAProxy version 2.3.0**


``handle_PROXY`` Hook
=====================

In addition to activating the PROXY protocol support as described above,
you MUST implement the ``handle_PROXY`` hook.
If the :attr:`handler` object does not implement ``handle_PROXY``,
then all connection attempts will be rejected.

The signature of ``handle_PROXY`` must be as follows:

.. method:: handle_PROXY(server, session, envelope, proxy_data)

   :param SMTP server: The :class:`SMTP` instance invoking the hook.
   :param Session session: The Session data *so far* (see Important note below)
   :param Envelope envelope: The Envelope data *so far* (see Important note below)
   :param ProxyData proxy_data: The result of parsing the PROXY Header
   :return: Truthy or Falsey, indicating if the connection may continue or not, respectively

   .. important::

      The ``session.peer`` attribute will contain the ``IP:port`` information
      of the **directly adjacent** client.
      In other word,
      it will contain the endpoint identifier of the proxying entity.

      Endpoint identifier of the "original" client will be recorded
      *only* in the :attr:`proxy_data` parameter

      The ``envelope`` data will usually be empty(ish),
      because the PROXY handshake will take place before
      client can send any transaction data.


Parsing the Header
==================

You do not have to concern yourself with parsing the PROXY Protocol header;
the ``aiosmtpd.proxy_protocol`` module contains the full parsing logic.

All you need to do is to *validate* the parsed result in the ``handle_PROXY`` hook.


``ProxyData`` API
=================

.. py:module:: aiosmtpd.proxy_protocol

.. py:class:: ProxyData(\
   version=None\
   )

   |
   | :part:`Attributes & Properties`

   .. py:attribute:: version
      :type: Optional[int]

      Contains the version of the PROXY Protocol header.

      If ``None``, it indicates that parsing has failed and the header is malformed.

   .. py:attribute:: command
      :type: int

      Contains the `command`_. Only set if ``version=2``

   .. py:attribute:: family
      :type: int

      Contains the `address family`_. Only set if ``version=2``

   .. py:attribute:: protocol
      :type: Union[bytes, int]

      For PROXY Header version 1,
      it contains a human-readable indication of the `INET protocol and family`_.

      For PROXY Header version 2,
      it contains an integer indicating the `transport protocol being proxied`_.

   .. py:attribute:: src_addr
      :type: Union[IPv4Address, IPv6Address, AnyStr]

      Contains the source address
      (i.e., address of the "original" client).

      The type of this attribute depends on the address family.

   .. py:attribute:: dst_addr
      :type: Union[IPv4Address, IPv6Address, AnyStr]

      Contains the destination address
      (i.e., address of the proxying entity to which the "original" client connected).

      The type of this attribute depends on the address family.

   .. py:attribute:: src_port
      :type: int

      Contains the source port
      (i.e., port of the "original" client).

      Valid only for address family of ``AF_INET`` or ``AF_INET6``

   .. py:attribute:: dst_port
      :type: int

      Contains the destination port
      (i.e., port of the proxying entity to which the "original" client connected).

      Valid only for address family of ``AF_INET`` or ``AF_INET6``

   .. py:attribute:: rest
      :type: Union[bytes, bytearray]

      The contents depend on the version of the PROXY header *and* (for version 2)
      the address family.

      For PROXY Header version 1,
      it contains all the bytes following ``b"UNKNOWN"`` up until, but not including,
      the ``CRLF`` terminator.

      For PROXY Header version 2:

        * For address family ``UNSPEC``,
          it contains all the bytes following the 16-octet header preamble
        * For address families ``AF_INET``, ``AF_INET6``, and ``UNIX``
          it contains all the bytes following the address information

   .. py:attribute:: tlv
      :type: aiosmtpd.proxy_protocol.ProxyTLV

      This property contains the result of the TLV Parsing attempt of the :attr:`rest` attribute.

      If ``None`` that means either (1) :attr:`rest` is empty, or (2) TLV Parsing is not successful.

   .. py:attribute:: valid
      :type: bool

      This property will indicate if PROXY Header is valid or not.

   |
   | :part:`Methods`

   .. py:method:: with_error(error_msg: str) -> ProxyData

      :param str error_msg: Error message
      :return: self

      Sets the instance's :attr:`error` attribute and returns itself.

   .. py:method:: same_attribs(**kwargs) -> bool

      A helper method to quickly verify whether an attribute exists
      and contain the same value as expected.

      Example usage::

         proxy_data.same_attribs(
             version=1,
             protocol=b"TCP4",
             unknown_attrib=None
         )

      In the above example,
      ``same_attribs`` will check that all attributes
      ``version``, ``protocol``, and ``unknown_attrib`` exist,
      and contains the values ``1``, ``b"TCP4"``, and ``None``, respectively.

      Missing attributes and/or differing values will return a ``False``

      .. note::

         For other examples, take a look inside the ``test_proxyprotocol.py`` file.
         That file *extensively* uses ``same_attribs``.

   .. py:method:: __bool__()

      Allows an instance of ``ProxyData`` to be evaluated as boolean.
      In actuality, it simply returns the :attr:`valid` property.


``ProxyTLV`` API
================

.. py:class:: ProxyTLV()

   This class parses the `TLV portion`_ of the PROXY Header
   and presents the value in an easy-to-use way:
   A "TLV Vector" whose "Type" is found in :attr:`PP2_TYPENAME`
   can be accessed through the `.<NAME>` attribute.

   It is a subclass of :class:`dict`,
   so all of ``dict``'s methods are available.
   It is basically a `Dict[str, Any]`.
   The list below only describes methods & attributes added to this class.

   .. py:attribute:: PP2_TYPENAME
      :type: Dict[int, str]

      A mapping of numeric Type to a human-friendly Name.

      The names are identical to the ones `listed in the documentation`_,
      but with the ``PP2_TYPE_``/``PP2_SUBTYPE_`` prefixes removed.

      .. note::

         The ``SSL`` Name is special.
         Rather than containing the TLV Subvectors as described in the standard,
         it is a ``bool`` value that indicates whether the PP2_SUBTYPE_SSL

   .. py:method:: same_attribs(**kwargs) -> bool

      A helper method to quickly verify whether an attribute exists
      and contain the same value as expected.

      Example usage::

         assert isinstance(proxy_tlv, ProxyTLV)
         proxy_tlv.same_attribs(
             AUTHORITY=b"some_authority",
             SSL=True,
         )

      In the above example,
      ``same_attribs`` will check that the attributes
      ``AUTHORITY`` and ``SSL`` exist,
      and contains the values ``b"some_authority"`` and ``True``, respectively.

      Missing attributes and/or differing values will return a ``False``

      .. note::

         For other examples, take a look inside the ``test_proxyprotocol.py`` file.
         That file *extensively* uses ``same_attribs``.

   .. py:classmethod:: from_raw(raw) -> Optional[ProxyTLV]

      :param raw: The raw bytes containing the TLV Vectors
      :type raw: Union[bytes, bytearray]
      :return: A new instance of ProxyTLV, or ``None`` if parsing failed

      This triggers the parsing of raw bytes/bytearray into a ProxyTLV instance.

      Internally it relies on the :meth:`parse` classmethod to perform the parsing.

      Unlike the default behavior of :meth:`parse`,
      ``from_raw`` will NOT perform a partial parsing.

   .. py:classmethod:: parse(chunk, partial_ok=True) -> Dict[str, Any]

      :param chunk: The bytes to parse into TLV Vectors
      :type chunk: Union[bytes, bytearray]
      :param partial_ok: If ``True``, return partially-parsed TLV Vectors as is.
         If ``False``, (re)raise ``MalformedTLV``
      :type partial_ok: bool
      :return: A mapping of typenames and values

      This performs a recursive parsing of the bytes.
      If it encounters a TYPE that ProxyTLV doesn't recognize,
      the TLV Vector will be assigned a typename of `"xNN"`

      Partial parsing is possible when ``partial_ok=True``;
      if during the parsing an error happened,
      `parse` will abort returning the TLV Vectors it had successfully decoded.

   .. py:classmethod:: name_to_num(name) -> Optional[int]

      :param name: The name to back-map into TYPE numeric
      :type name: str
      :return: The numeric value associated to the typename, ``None`` if no such mapping is found

      This is a helper method to perform back-mapping of typenames.


.. _`command`: https://github.com/haproxy/haproxy/blob/1c0a722a83e7c45456a2b82c15889ab9ab5c4948/doc/proxy-protocol.txt#L346-L358
.. _`address family`: https://github.com/haproxy/haproxy/blob/1c0a722a83e7c45456a2b82c15889ab9ab5c4948/doc/proxy-protocol.txt#L366-L381
.. _`INET protocol and family`:  https://github.com/haproxy/haproxy/blob/1c0a722a83e7c45456a2b82c15889ab9ab5c4948/doc/proxy-protocol.txt#L207-L213
.. _`transport protocol being proxied`: https://github.com/haproxy/haproxy/blob/1c0a722a83e7c45456a2b82c15889ab9ab5c4948/doc/proxy-protocol.txt#L388-L402
.. _TLV portion: https://github.com/haproxy/haproxy/blob/1c0a722a83e7c45456a2b82c15889ab9ab5c4948/doc/proxy-protocol.txt#L519
.. _listed in the documentation: https://github.com/haproxy/haproxy/blob/1c0a722a83e7c45456a2b82c15889ab9ab5c4948/doc/proxy-protocol.txt#L538-L549