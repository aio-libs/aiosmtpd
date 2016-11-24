===================
 NEWS for aiosmtpd
===================

1.0a3 (20XX-XX-XX)
==================
* Fix typo in `Message.prepare_message()` handler.  The crafted `X-RcptTos`
  header is renamed to `X-RcptTo` for backward compatibility with older
  libraries.

1.0a2 (2016-11-22)
==================
* Officially support Python 3.6.
* Fix support for both IPv4 and IPv6 based on the --listen option.  Given by
  Jason Coombs.  (Closes #3)
* Correctly handle client disconnects.  Given by Konstantin vz'One Enchant.
* The SMTP class now takes an optional `hostname` argument.  Use this if you
  want to avoid the use of `socket.getfqdn()`.  Given by Konstantin vz'One
  Enchant.
* Close the transport and thus the connection on SMTP QUIT.  (Closes #11)
* Added an AsyncMessage handler.  Given by Konstantin vz'One Enchant.
* Add an examples/ directory.
* Flake8 clean.

1.0a1 (2015-10-19)
==================
* Initial release.
