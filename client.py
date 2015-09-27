from smtplib import *

s = SMTP()
s.connect('localhost', 9978)
s.sendmail('anne@example.com', ['bart@example.com'], """\
From: anne@example.com
To: bart@example.com
Subject: A test

testing
""")
s.quit()
