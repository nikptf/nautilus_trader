# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2021 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------

import sys

import fsspec
import pytest
from dask.utils import parse_bytes

from nautilus_trader.adapters.betfair.providers import BetfairInstrumentProvider
from nautilus_trader.backtest.config import BacktestDataConfig
from nautilus_trader.backtest.config import BacktestRunConfig
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.model.data.venue import InstrumentStatusUpdate
from nautilus_trader.persistence.batching import batch_files
from nautilus_trader.persistence.catalog import DataCatalog
from nautilus_trader.persistence.external.core import process_files
from nautilus_trader.persistence.external.readers import CSVReader
from tests.integration_tests.adapters.betfair.test_kit import BetfairTestStubs
from tests.test_kit import PACKAGE_ROOT
from tests.test_kit.mocks import NewsEventData
from tests.test_kit.mocks import data_catalog_setup
from tests.test_kit.stubs import TestStubs


TEST_DATA_DIR = PACKAGE_ROOT + "/data"


@pytest.mark.skipif(sys.platform == "win32", reason="test path broken on windows")
class TestPersistenceBatching:
    def setup(self):
        data_catalog_setup()
        self.catalog = DataCatalog.from_env()
        self.fs: fsspec.AbstractFileSystem = self.catalog.fs
        self._loaded_data_into_catalog()

    def _loaded_data_into_catalog(self):
        self.instrument_provider = BetfairInstrumentProvider.from_instruments([])
        process_files(
            glob_path=PACKAGE_ROOT + "/data/1.166564490.bz2",
            reader=BetfairTestStubs.betfair_reader(instrument_provider=self.instrument_provider),
            instrument_provider=self.instrument_provider,
            catalog=self.catalog,
        )

    def test_batch_files_single(self):
        # Arrange
        instrument_ids = self.catalog.instruments()["id"].unique().tolist()
        base = BacktestDataConfig(
            catalog_path=str(self.catalog.path),
            catalog_fs_protocol=self.catalog.fs.protocol,
            data_cls_path="nautilus_trader.model.orderbook.data.OrderBookData",
        )

        iter_batches = batch_files(
            catalog=self.catalog,
            data_configs=[
                base.replace(instrument_id=instrument_ids[0]),
                base.replace(instrument_id=instrument_ids[1]),
            ],
            target_batch_size_bytes=parse_bytes("10kib"),
            read_num_rows=300,
        )

        # Act
        timestamp_chunks = []
        for batch in iter_batches:
            timestamp_chunks.append([b.ts_init for b in batch])

        # Assert
        latest_timestamp = 0
        for timestamps in timestamp_chunks:
            assert max(timestamps) > latest_timestamp
            latest_timestamp = max(timestamps)
            assert timestamps == sorted(timestamps)

    def test_batch_generic_data(self):
        # Arrange
        TestStubs.setup_news_event_persistence()
        process_files(
            glob_path=f"{PACKAGE_ROOT}/data/news_events.csv",
            reader=CSVReader(block_parser=TestStubs.news_event_parser),
            catalog=self.catalog,
        )
        data_config = BacktestDataConfig(
            catalog_path="/root/",
            catalog_fs_protocol="memory",
            data_cls_path=f"{NewsEventData.__module__}.NewsEventData",
            client_id="NewsClient",
        )
        # Add some arbitrary instrument data to appease BacktestEngine
        instrument_data_config = BacktestDataConfig(
            catalog_path="/root/",
            catalog_fs_protocol="memory",
            instrument_id=self.catalog.instruments(as_nautilus=True)[0].id.value,
            data_cls_path=f"{InstrumentStatusUpdate.__module__}.InstrumentStatusUpdate",
        )
        run_config = BacktestRunConfig(
            data=[data_config, instrument_data_config],
            persistence=BetfairTestStubs.persistence_config(catalog_path=self.catalog.path),
            venues=[BetfairTestStubs.betfair_venue_config()],
            strategies=[],
            batch_size_bytes=parse_bytes("1mib"),
        )

        # Act
        node = BacktestNode()
        node.run_sync([run_config])

        # Assert
        assert node
