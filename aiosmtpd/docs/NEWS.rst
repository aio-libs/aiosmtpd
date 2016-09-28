===================
 NEWS for aiosmtpd
===================

1.0a2 (20XX-XX-XX)
==================
* Fix support for both IPv4 and IPv6 based on the --listen option.  Given by
  Jason Coombs.  (Closes #3)
* Correctly handle client disconnects.  Given by Konstantin vz'One Enchant.
* Close the transport and thus the connection on SMTP QUIT.  (Closes #11)
* Add an examples/ directory.
* Flake8 clean.

1.0a1 (2015-10-19)
==================
* Initial release.
