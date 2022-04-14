from __future__ import annotations
from abc import ABC, abstractmethod
from numpy import fliplr
import pyodbc
import xlrd
from xlrd.sheet import ctype_text 
from io import SEEK_END
from os import listdir, rename, sep, SEEK_SET
from os.path import isfile, join, isdir, splitext
import datetime
import os
import csv

class Filepath(str):

    def __init__ (self, value):
        self.value = value

    def _replase_bars(self, path:str)->str:
        path = path.replace("\\", "/").replace('/', os.sep)
        return path

    def __str__(self)->str:
        return self._replase_bars(self.value)

    def get_filename(self, remove_ext=False)->str:
        filename = os.path.basename(self.value)
        if remove_ext:
            filename = os.path.splitext(filename)[0]
        return filename

    def add_suffix(self, sufix:str)->Filepath:
        s = os.path.splitext(self.value)
        return Filepath(s[0] + sufix + s[1])

    def get_dir(self):
        s = os.path.splitext(self.value)
        if s[0].endswith(os.sep):
            return s[0][:-len(os.sep)]
        return s[0]

    def append_dir(self, path:str)->Filepath:
        return Filepath(self.get_dir() + "/" + path.strip(os.sep) + '/' + self.get_filename())

    def append_file(self, filename:str)->Filepath:
        return Filepath(self.get_dir() + "/" + filename)

class Conn(ABC):

    def __init__(self):
        self._handler = None
        self.connect()

    def connect(self):
        if not self._handler:
            self._handler = self._connect()

    def disconnect(self):
        if self._handler:
            self._disconnect()

    def get_handler(self):
        return self._handler

    @abstractmethod
    def _connect(self):
        pass

    @abstractmethod
    def _disconnect(self):
        pass

class SQLServer(Conn):

    conn_string = 'DRIVER={ODBC Driver 13 for SQL Server};SERVER=%s;DATABASE=%s;UID=%s;PWD=%s'

    def __init__(self
        , host:str
        , db:str
        , user:str
        , pwd:str):
        self.host = host
        self.db = db
        self.user = user
        self.pwd = pwd
        self._cursor = None
        super().__init__()

    def _connect(self):
        return pyodbc.connect(SQLServer.conn_string % (self.host, self.db, self.user, self.pwd))

    def _disconnect(self):
        self.commit()
        self.close()
        self._handler.close()

    def open(self):
        if self._cursor:
            self.commit()
            self.close()
        self._cursor = self._handler.cursor()

    def commit(self):
        if self._cursor:
            self._cursor.commit()

    def close(self):
        if self._cursor:
            self._cursor.close()
            self._cursor = None

    def query(self, sql:str, data:list=None):
        if not self._cursor:
            raise Exception("Cursor nulo. Eh preciso abrir cursor com metodo open().")
        if data:
            return self._cursor.execute(sql, data)
        return self._cursor.execute(sql)

    def trans_query(self, sql:str, data:list=None):
        self.open()
        self.query(sql, data)
        self.commit()
        self.close()

    def resize(self
        , tablename:str
        , fieldname:str
        , size:int):
        if not size:
            return
        if size>4000:
            size = 'MAX'
        sql = "ALTER TABLE [{}] ALTER COLUMN [{}] VARCHAR({})".format(tablename, fieldname, size)
        return self.trans_query(sql)

    def truncate(self, tablename:str):
        return self.trans_query("TRUNCATE TABLE {};".format(tablename))

    def all(self, tablename:str)->list:
        self.query("SELECT * FROM {}".format(tablename))
        return self._cursor.fetchall()

    def insert(self, tablename:str, data:dict):
        field_list = data.keys()
        values = data.values()
        sql = "INSERT INTO dbo.{} ([{}]) VALUES ({})".format(tablename
            , '],['.join(field_list)
            , ', '.join(["?"]*len(field_list)))
        self.query(sql, list(values))
        # print("Inseridos {} registros.".format(len(values)))
        return self

    def bulk_insert(self, tablename:str, field_list:list, data:list):
        sql = "INSERT INTO dbo.{} ([{}]) VALUES ({})".format(tablename
            , '],['.join(field_list)
            , ', '.join(["?"]*len(field_list)))
        if data:
            self._cursor.executemany(sql, data)
        return self

    def field_list(self, tablename:str):
        self.open()
        self.query("SELECT TOP 1 * FROM {}".format(tablename))
        columns = [column[0] for column in self._cursor.description]
        self.close()
        return columns

    def field_size(self, tablename:str)->dict:
        self.open()
        self.query("SELECT column_name, data_type, character_maximum_length FROM information_schema.columns WHERE table_name='{}'".format(tablename))
        field_size = {}
        for row in self._cursor.fetchall():
            field_size[row[0]] = row[2]
        self.close()
        return field_size




class DirectoryConn(Conn):

    @staticmethod
    def sources(path:str, filter:str=None)->list:
        return [f for f in listdir(path) if isfile(join(path, f)) and (not filter or f.startswith(filter))]

    def __init__(self, path:str):
        self._path = Filepath(path)
        super().__init__()
        
    def __str__(self)->str:
        return str(self._path)

    def select(self, filter:str=None)->list:
        return DirectoryConn.sources(self._path)

    def _connect(self):
        if isdir(self._path) is False:
            raise Exception("Caminho {} nao existe.".format(self._path))
        return True

    def _disconnect(self):
        self._handler = None

class ExcelConn(Conn):

    @staticmethod
    def sources(path:str
        , filename:str
        , filter:str=None)->list:
        filepath = join(path, filename)
        wb = xlrd.open_workbook(filepath)
        return [f for f in wb.sheet_names() if not filter or f.startswith(filter)]

    def __init__(self
        , path:str
        , filename:str
        , sheet:str
        , has_header:bool=True
        , encode:str='utf-8'):
        self._filepath = join(path, filename)
        self._wb = None
        self._sheet = sheet
        self._has_header = has_header
        self._encode = encode
        super().__init__()

    def get_filepath(self)->str:
        return self._filepath

    def _connect(self):
        self._wb = xlrd.open_workbook(self._filepath)
        sheet = self._wb.sheet_by_name(self._sheet)
        return sheet

    def _disconnect(self):
        self._handler = None
        self._wb = None

    def get_type_list(self):
        type_list = []
        row_idx = int(self._has_header)
        row = self._handler.row(row_idx)
        for cell_obj in row:
            cell_type_str = ctype_text.get(cell_obj.ctype, 'unknown type')
            type_list.append(cell_type_str)
        return type_list

    def get_col_names(self):
        ncols = self._handler.ncols
        if self._has_header:
            cols = self._handler.row_values(0)
            if len(cols)<ncols:
                cols += [str(i) for i in range(len(cols), ncols)]
            return cols
        return [str(i) for i in range(0, ncols)]

    def __parse_row_value(self, row:list, type_list:list):
        return [self.__parse_cell_value(j, v, type_list) for j, v in enumerate(row)]

    def __parse_cell_value(self, col_idx:int, value, type_list:str):
        type = type_list[col_idx]
        if type=='xldate':
            return datetime.datetime(*xlrd.xldate_as_tuple(value, self._wb.datemode))
        elif type=='empty':
            return None
        elif type=='number':
            return float(value)
        return value

    def _get_row(self, row_num:int, type_list:list):
        row = self._handler.row_values(row_num)
        return [self.__parse_cell_value(col_idx, v, type_list) for col_idx, v in enumerate(row)]

    def all(self)->list:
        """
        Metodo para carregar o dataset.
        """
        r = []
        type_list = self.get_type_list()
        first_row = 1 if self._has_header else 0
        for row_num in range(first_row, self._handler.nrows):
            r.append(self._handler.row_values(row_num))
            # r.append(self._get_row(row_num, type_list))
        return r

class FlatFileConn(Conn):

    def __init__(self
        , path:str
        , filename:str
        , mode:str='r'
        , encode:str='utf-8'):
        self._dir_conn = DirectoryConn(path)
        self._filename = filename
        self._mode = mode
        self._encode = encode
        super().__init__()
        
    def __str__(self)->str:
        return self.get_filepath()

    def get_filepath(self)->str:
        """
        ...
        """
        return os.path.join(str(self._dir_conn), self._filename)

    def get_filename(self)->str:
        return self._filename

    def _connect(self):
        filepath = self._dir_conn._path.append_file(self._filename)
        handler = open(str(filepath), self._mode, encoding=self._encode)
        handler.seek(0, SEEK_END) # go to the file end.
        #if handler.tell()>3:
        #    handler.seek(handler.tell() - 3, SEEK_SET)
        self.__eof = handler.tell() # get the end of file location
        # print(self._filename, self.__eof)
        handler.seek(0, 0) # go back to file beginning
        return handler

    def _disconnect(self):
        self._handler.close()

    def eof(self)->bool:
        return self._handler.tell()==self.__eof

    def all(self)->list:
        """
        Metodo para carregar o dataset.
        """
        r = []
        # row = self.get_handler().readline()
        while not self.eof():
            row = self.get_handler().readline()
            r.append([row])
        self.get_handler().seek(0)
        return r

    def check_content(self, ctn:str)->bool:
        """
        Verifica se o arquivo possui
        alguma linha que comece com o 
        conteudo passado.
        """
        finded = False
        self.get_handler().seek(0, 0)
        while not self.eof():
            row = self.get_handler().readline()
            if row.startswith(ctn):
                finded = True
                break
        self.get_handler().seek(0, 0)
        return finded

    # TODO: arrumar isso
    def rename(self, newname:str):
        filepath = self._dir_conn._path.append_file(self._filename)
        path = Filepath(newname)
        s = splitext(path)
        path = s[0]
        filename = s[1]
        if path.endswith(sep):
            return path[:-len(sep)]
        if not filename:
            filename = filepath.get_filename()
        self.disconnect()
        new_path = Filepath(path + "/" + filename)
        rename(filepath, new_path)
        self._filepath = new_path
        self.connect()
        return self

    def is_empty(self):
        """
        Check if the source is empty.
        """
        return os.stat(self.get_filepath()).st_size==0

class CSVConn(Conn):

    def __init__(self
        , path:str
        , filename:str
        , mode='r'
        , encode='utf-8'
        , has_header=True
        , delimiter=','
        , quote='"'
        , default_col_name='col{}'):
        """
        ...
        """
        self._path = path
        self._filename = filename
        self._mode = mode
        self._encode = encode
        self._has_header = has_header
        self._delimiter = delimiter
        self._quote = quote
        self._default_col_name = default_col_name
        super().__init__()

    def eof(self)->bool:
        """
        ...
        """
        return self._handler.eof()

    def get_filepath(self)->str:
        """
        ...
        """
        return self._handler.get_filepath()

    def field_list(self):
        """
        If the CSV has header, return the
        first row. Otherwise create cols
        name using "_default_col_name" and
        the number of columns in the CSV.
        """
        self._handler.get_handler().seek(0)
        reader = csv.reader(self._handler.get_handler(), delimiter=self._delimiter, quotechar=self._quote)
        row = next(reader)
        if self._has_header:
            return row
        else:
            return [self._default_col_name.format(i+1) for i in range(len(row))]

    def _list_to_dict(self, data:list):
        """
        TODO: criar um metodo para tratar os dados
        a serem inseridos.
        """
        if type(data) is not list:
            raise Exception("Variable 'data' is not a list. Only lists can be converted to a dictionary.")
        flist = self.field_list()
        to_insert = {}
        for i, colval in enumerate(data):
            to_insert[flist[i]] = colval 
        return to_insert

    def _check_dict_keys(self, data_dict:dict, allowed_keys:list):
        """
        Dictionaries are used to reference
        rows of data. If any dictionary
        key is not part of the field list,
        it throws an error.
        """
        # check if the keys are fields in field list
        wrong_keys = []
        keys = data_dict.keys()
        for k in keys:
            if k not in allowed_keys:
                wrong_keys.append(k)
        return wrong_keys

    def is_empty(self):
        """
        Check if the source is empty.
        """
        return os.stat(self.get_filepath()).st_size==0

    def insert_list(self, data:list):
        """
        ...
        """
        # TODO: sera que os valores chegaram na mesma
        # ordem que foram passados?
        if type(data) is not list:
            raise Exception("Data is not a list.")
        # if CSV has header but the file is empty, write the fields in the first line
        if self._has_header and self.is_empty():
            self.insert_list(self.field_list())
        w = csv.writer(self._handler.get_handler(), delimiter=self._delimiter, quotechar=self._quote)
        w.writerow(data)

    def insert_dict(self, data:dict):
        """
        ...
        """
        if type(data) is not dict:
            raise Exception("The data is not a dictionary.")
        header = self.field_list()
        # if CSV has header but the file is empty, write the fields in the first line
        if self._has_header and self.is_empty():
            self.insert_list(header)
        wrong_keys = self._check_dict_keys(data_dict=data, allowed_keys=header)
        if wrong_keys:
            raise Exception("The key {} doesn't exists on the field list.".format(wrong_keys[0]))
        w = csv.DictWriter(self._handler.get_handler(), fieldnames=header, delimiter=self._delimiter, quotechar=self._quote)
        w.writerow(data)

    def insert(self, data):
        """
        ...
        """
        if type(data) is dict:
            self._insert_dict(data) if self._has_header else self._insert_list(data.values())
        elif type(data) is list:
            self._insert_list(data.values())
        else:
            raise Exception("Data needs to be in a list or dictionaty.")

    def bulk_insert(self, data:list):
        """
        ...
        """
        if data:
            # if CSV has header but the file is empty, write the fields in the first line
            if self._has_header and self.is_empty():
                self.insert_list(self.field_list())
            rtype = type(data[0])
            if rtype is dict:
                writer = csv.DictWriter(self._handler.get_handler()
                    , fieldnames=self.field_list()
                    , delimiter=self._delimiter
                    , quotechar=self._quote)
            elif rtype is list:
                writer = csv.writer(self._handler.get_handler()
                    , delimiter=self._delimiter
                    , quotechar=self._quote)
            else:
                raise Exception("Data needs to be in a list or dictionaty.")
            # insert all data usign the right writer
            for d in data:
                writer.writerow(d)

    def all(self)->list:
        """
        Return all rows in the CSV.
        If the CSV has header, the
        header info is excluded from
        results.
        """
        all = []
        # retorns the file cursor to the first position
        self._handler.get_handler().seek(0)
        reader = csv.reader(self._handler.get_handler(), delimiter=self._delimiter, quotechar=self._quote)
        # if the file has header, jump the first row
        if self._has_header:
            next(reader)
        for row in reader:
            all.append(row)
        return all

    def get_handler(self):
        return self._handler.get_handler()

    def _connect(self):
        flat_conn = FlatFileConn(path=self._path
            , filename=self._filename
            , mode=self._mode
            , encode=self._encode)
        return flat_conn

    def _disconnect(self):
        self._handler.disconnect()
        self._handler = None


def Factory(driver:str, **kwargs):
 
    localizers = {
        "sqlserver": SQLServer,
        "flatfile": FlatFileConn,
        "excel": ExcelConn,
        "csv": CSVConn,
        "dir": DirectoryConn
    }
 
    if localizers.get(driver, None) is None:
        raise Exception("Não é possível instanciar um objeto que não existe na lista de Factory.")
    return localizers[driver](**kwargs)

class Storagy(object):

    def __init__(self, driver:str, **kwargs):
        self._driver = Factory(driver, **kwargs)

    def all(self):
        return self._driver.all()

    def field_list(self):
        return self._driver.field_list()

    def is_empty(self):
        return self._driver.is_empty()

