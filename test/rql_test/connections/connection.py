##
# Tests the driver API for making connections and excercizes the networking code
###

from sys import argv
from subprocess import Popen
from time import sleep
from sys import path, exit
import unittest
path.insert(0, '.')
from test_util import RethinkDBTestServers
path.insert(0, "../../drivers/python")

# We import the module both ways because this used to crash and we
# need to test for it
from rethinkdb import *
import rethinkdb as r

server_build_dir = argv[1]
use_default_port = bool(int(argv[2]))


class TestNoConnection(unittest.TestCase):
    # No servers started yet so this should fail
    def test_connect(self):
        self.assertRaisesRegexp(
            RqlDriverError, "Could not connect to localhost:28015.",
            r.connect)

    def test_connect_port(self):
        self.assertRaisesRegexp(
            RqlDriverError, "Could not connect to localhost:11221.",
            r.connect, port=11221)

    def test_connect_host(self):
        self.assertRaisesRegexp(
            RqlDriverError, "Could not connect to 0.0.0.0:28015.",
            r.connect, host="0.0.0.0")

    def test_connect_host(self):
        self.assertRaisesRegexp(
            RqlDriverError, "Could not connect to 0.0.0.0:11221.",
            r.connect, host="0.0.0.0", port=11221)

    def test_empty_run(self):
        # Test the error message when we pass nothing to run and
        # didn't call `repl`
        self.assertRaisesRegexp(
            r.RqlDriverError, "RqlQuery.run must be given a connection to run on.",
            r.expr(1).run)

class TestConnectionDefaultPort(unittest.TestCase):

    def setUp(self):
        if not use_default_port:
            skipTest("Not testing default port")
        self.servers = RethinkDBTestServers(4, server_build_dir=server_build_dir, use_default_port=use_default_port)
        self.servers.__enter__()

    def tearDown(self):
        self.default_server.__exit__(None, None, None)

    def test_connect(self):
        conn = r.connect()
        conn.reconnect()

    def test_connect_host(self):
        conn = r.connect(host='localhost')
        conn.reconnect()

    def test_connect_host_port(self):
        conn = r.connect(host='localhost', port=28015)
        conn.reconnect()

    def test_connect_port(self):
        conn = r.connect(port=28015)
        conn.reconnect()

class TestWithConnection(unittest.TestCase):

    def setUp(self):
        self.servers = RethinkDBTestServers(4, server_build_dir=server_build_dir)
        self.servers.__enter__()
        self.port = self.servers.driver_port()

    def tearDown(self):
        self.servers.__exit__(None, None, None)

class TestConnection(TestWithConnection):
    def test_connect_close_reconnect(self):
        c = r.connect(port=self.port)
        r.expr(1).run(c)
        c.close()
        c.close()
        c.reconnect()
        r.expr(1).run(c)

    def test_connect_close_expr(self):
        c = r.connect(port=self.port)
        r.expr(1).run(c)
        c.close()
        self.assertRaisesRegexp(
            r.RqlDriverError, "Connection is closed.",
            r.expr(1).run, c)

    def test_db(self):
        c = r.connect(port=self.port)

        r.db('test').table_create('t1').run(c)
        r.db_create('db2').run(c)
        r.db('db2').table_create('t2').run(c)

        # Default db should be 'test' so this will work
        r.table('t1').run(c)

        # Use a new database
        c.use('db2')
        r.table('t2').run(c)
        self.assertRaisesRegexp(
            r.RqlRuntimeError, "Table `t1` does not exist.",
            r.table('t1').run, c)

        c.use('test')
        r.table('t1').run(c)
        self.assertRaisesRegexp(
            r.RqlRuntimeError, "Table `t2` does not exist.",
            r.table('t2').run, c)

        c.close()

        # Test setting the db in connect
        c = r.connect(db='db2', port=self.port)
        r.table('t2').run(c)

        self.assertRaisesRegexp(
            r.RqlRuntimeError, "Table `t1` does not exist.",
            r.table('t1').run, c)

        c.close()

        # Test setting the db as a `run` option
        c = r.connect(port=self.port)
        r.table('t2').run(c, db='db2')

    def test_use_outdated(self):
        c = r.connect(port=self.port)
        r.db('test').table_create('t1').run(c)

        # Use outdated is an option that can be passed to db.table or `run`
        # We're just testing here if the server actually accepts the option.

        r.table('t1', use_outdated=True).run(c)
        r.table('t1').run(c, use_outdated=True)

    def test_repl(self):

        # Calling .repl() should set this connection as global state
        # to be used when `run` is not otherwise passed a connection.
        c = r.connect(port=self.port).repl()

        r.expr(1).run()

        c.repl() # is idempotent

        r.expr(1).run()

        c.close()

        self.assertRaisesRegexp(
            r.RqlDriverError, "Connection is closed",
            r.expr(1).run)

class TestShutdown(TestWithConnection):
    def test_shutdown(self):
        c = r.connect(port=self.port)
        r.expr(1).run(c)
        self.servers.stop()
        sleep(0.2)
        self.assertRaisesRegexp(
            r.RqlDriverError, "Connection is closed.",
            r.expr(1).run, c)


# This doesn't really have anything to do with connections but it'll go
# in here for the time being.
class TestPrinting(unittest.TestCase):

    # Just test that RQL queries support __str__ using the pretty printer.
    # An exhaustive test of the pretty printer would be, well, exhausing.
    def runTest(self):
        self.assertEqual(str(r.db('db1').table('tbl1').map(lambda x: x)),
                            "r.db('db1').table('tbl1').map(lambda var_1: var_1)")

class TestBatching(TestWithConnection):
    def runTest(self):
        c = r.connect(port=self.port)

        # Test the cursor API when there is exactly mod batch size elements in the result stream
        r.db('test').table_create('t1').run(c)
        t1 = r.table('t1')

        if server_build_dir.find('debug') != -1:
            batch_size = 5
        else:
            batch_size = 1000

        t1.insert([{'id':i} for i in xrange(0, batch_size)]).run(c)
        cursor = t1.run(c)

        # We're going to have to inspect the state of the cursor object to ensure this worked right
        # If this test fails in the future check first if the structure of the object has changed.

        # Only the first chunk (of either 1 or 2) should have loaded
        self.assertEqual(len(cursor.chunks), 1)

        # Either the whole stream should have loaded in one batch or the server reserved at least
        # one element in the stream for the second batch.
        if cursor.end_flag:
            self.assertEqual(len(cursor.chunks[0]), batch_size)
        else:
            assertLess(len(cursor.chunks[0]), batch_size)

        itr = iter(cursor)
        for i in xrange(0, batch_size - 1):
            itr.next()

        # In both cases now there should at least one element left in the last chunk
        self.assertTrue(cursor.end_flag)
        self.assertGreaterEqual(len(cursor.chunks), 1)
        self.assertGreaterEqual(len(cursor.chunks[0]), 1)

# # TODO: test cursors, streaming large values

if __name__ == '__main__':
    print "Running py connection tests"
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    suite.addTest(loader.loadTestsFromTestCase(TestNoConnection))
    suite.addTest(loader.loadTestsFromTestCase(TestConnection))
    suite.addTest(loader.loadTestsFromTestCase(TestShutdown))
    suite.addTest(TestPrinting())
    suite.addTest(TestBatching())

    res = unittest.TextTestRunner(verbosity=2).run(suite)

    if not res.wasSuccessful():
        exit(1)
