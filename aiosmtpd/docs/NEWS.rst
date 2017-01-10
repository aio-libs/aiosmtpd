===================
 NEWS for aiosmtpd
===================

1.0a5 (201X-XX-XX)
==================
* Added the ``STARTTLS`` extension.  Given by Konstantin Volkov.
* Minor changes to the way the ``Debugging`` handler prints ``mail_options``
  and ``rcpt_options`` (although the latter is still not support in ``SMTP``).
* Use ``flufl.testing`` package for nose2 and flake8 plugins.
* Better Python 3.4 compatibility.

1.0a4 (2016-11-29)
==================
* The SMTP server connection identifier can be changed by setting the
  ``__ident__`` attribute on the ``SMTP`` instance.  (Closes #20)
* Fixed a new incompatibility with the ``atpublic`` library.

1.0a3 (2016-11-24)
==================
* Fix typo in ``Message.prepare_message()`` handler.  The crafted
  ``X-RcptTos`` header is renamed to ``X-RcptTo`` for backward compatibility
  with older libraries.
* Add a few hooks to make subclassing easier:
  - ``SMTP.ehlo_hook()`` is called just before the final, non-continuing 250
    response to allow subclasses to add additional ``EHLO`` sub-responses.
  - ``SMTP.rset_hook()`` is called just before the final 250 command to allow
    subclasses to provide additional ``RSET`` functionality.
  - ``Controller.make_socket()`` allows subclasses to customize the creation
    of the socket before binding.

1.0a2 (2016-11-22)
==================
* Officially support Python 3.6.
* Fix support for both IPv4 and IPv6 based on the ``--listen`` option.  Given
  by Jason Coombs.  (Closes #3)
* Correctly handle client disconnects.  Given by Konstantin vz'One Enchant.
* The SMTP class now takes an optional ``hostname`` argument.  Use this if you
  want to avoid the use of ``socket.getfqdn()``.  Given by Konstantin vz'One
  Enchant.
* Close the transport and thus the connection on SMTP ``QUIT``.  (Closes #11)
* Added an ``AsyncMessage`` handler.  Given by Konstantin vz'One Enchant.
* Add an examples/ directory.
* Flake8 clean.

1.0a1 (2015-10-19)
==================
* Initial release.
