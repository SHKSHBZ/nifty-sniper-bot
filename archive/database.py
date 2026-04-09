from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

Base = declarative_base()

class Trade(Base):
    __tablename__ = 'trades'
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    symbol = Column(String)
    side = Column(String)
    quantity = Column(Float)
    price = Column(Float)
    target = Column(Float)
    stop = Column(Float)
    status = Column(String) # FILLED, CLOSED
    pnl = Column(Float, default=0.0)

class Performance(Base):
    __tablename__ = 'performance'
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    nifty_balance = Column(Float)
    bank_balance = Column(Float)
    nifty_pnl = Column(Float)
    bank_pnl = Column(Float)
    total_trades = Column(Integer)
    open_positions = Column(JSON)

class Tick(Base):
    __tablename__ = 'ticks_history'
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    symbol = Column(String)
    ltp = Column(Float)
    ofi = Column(Float)
    rsi = Column(Float)

class TradingDB:
    def __init__(self, db_url="sqlite:///data/trading_v2.db"):
        self.engine = create_engine(db_url)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def log_trade(self, symbol, side, quantity, price, target, stop):
        session = self.Session()
        trade = Trade(symbol=symbol, side=side, quantity=quantity, 
                      price=price, target=target, stop=stop, status='FILLED')
        session.add(trade)
        session.commit()
        trade_id = trade.id
        session.close()
        return trade_id

    def close_trade(self, symbol, exit_price, pnl):
        session = self.Session()
        trade = session.query(Trade).filter_by(symbol=symbol, status='FILLED').order_by(Trade.timestamp.desc()).first()
        if trade:
            trade.status = 'CLOSED'
            trade.pnl = pnl
            session.commit()
        session.close()

    def log_performance(self, nifty_bal, bank_bal, nifty_pnl, bank_pnl, trades, positions):
        session = self.Session()
        perf = Performance(nifty_balance=nifty_bal, bank_balance=bank_bal, 
                           nifty_pnl=nifty_pnl, bank_pnl=bank_pnl, total_trades=trades,
                           open_positions=positions)
        session.add(perf)
        session.commit()
        session.close()

    def log_tick(self, symbol, ltp, ofi, rsi):
        session = self.Session()
        tick = Tick(symbol=symbol, ltp=ltp, ofi=ofi, rsi=rsi)
        session.add(tick)
        session.commit()
        session.close()

    def get_latest_performance(self):
        """Retrieve the last saved performance metrics for carry-forward."""
        session = self.Session()
        last_perf = session.query(Performance).order_by(Performance.timestamp.desc()).first()
        if last_perf:
            data = {
                'nifty_bal': last_perf.nifty_balance,
                'bank_bal': last_perf.bank_balance,
                'nifty_pnl': last_perf.nifty_pnl,
                'bank_pnl': last_perf.bank_pnl,
                'total_trades': last_perf.total_trades,
                'open_positions': last_perf.open_positions
            }
            session.close()
            return data
        session.close()
        return None
