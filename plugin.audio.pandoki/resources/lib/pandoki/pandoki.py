import collections, re, socket, sys, threading, time, urllib, urllib2, os
import xbmc, xbmcaddon, xbmcgui, xbmcplugin, xbmcvfs
import asciidamnit, musicbrainzngs, mypithos

try:
    import urllib3
    import urllib3.contrib.pyopenssl
    urllib3.contrib.pyopenssl.inject_into_urllib3()
    _urllib3 = True
except ImportError:
    _urllib3 = False
    pass

from mutagen.mp3 import MP3
from mutagen.easyid3 import EasyID3
from mutagen.easymp4 import EasyMP4



_addon	= xbmcaddon.Addon()
_base	= sys.argv[0]
_id	= _addon.getAddonInfo('id')
_stamp	= str(time.time())


def Log(msg, s = None, level = xbmc.LOGNOTICE):
    if s and s.get('artist'): xbmc.log("%s %s %s '%s - %s'" % (_id, msg, s['token'][-4:], s['artist'], s['title']), level) # song
    elif s:                   xbmc.log("%s %s %s '%s'"      % (_id, msg, s['token'][-4:], s['title']), level)              # station
    else:                     xbmc.log("%s %s"              % (_id, msg), level)

# setup the ability to provide notification to the Kodi GUI
iconart = xbmc.translatePath(os.path.join('special://home/addons/plugin.audio.pandoki',  'icon.png'))

def notification(title, message, ms, nart):
    xbmc.executebuiltin("XBMC.notification(" + title + "," + message + "," + ms + "," + nart + ")")


def Val(key, val = None):
    if key in [ 'author', 'changelog', 'description', 'disclaimer', 'fanart', 'icon', 'id', 'name', 'path', 'profile', 'stars', 'summary', 'type', 'version' ]:
        return _addon.getAddonInfo(key)

    if val:      _addon.setSetting(key, val)
    else: return _addon.getSetting(key)


def Prop(key, val = 'get'):
    if val == 'get': 
        retVal = xbmcgui.Window(10000).getProperty("%s.%s" % (_id, key))
        Log('def Prop %s=%s value=%s' % (key, val, retVal), None, xbmc.LOGDEBUG)
        return retVal
    else:
        Log('def Prop %s=%s ' % (key, val), None, xbmc.LOGDEBUG)
        xbmcgui.Window(10000).setProperty("%s.%s" % (_id, key), val)


_maxdownloads=int(Val('maxdownload'))

class Pandoki(object):
    def __init__(self):
        run = Prop('run')
        if (run) and (time.time() < float(run) + 3): return

        Prop('run', str(time.time()))
        Prop('stamp', _stamp)

        self.once	= True
        self.downloading = 0  # number of files currently being downloaded
        self.abort	= False
        self.mesg	= None
        self.station	= None
        self.stations	= None
        self.songs	= { }
        self.pithos	= mypithos.Pithos()
        self.player	= xbmc.Player()
        self.playlist	= xbmc.PlayList(xbmc.PLAYLIST_MUSIC)
        self.ahead	= { }
        self.queue	= collections.deque()
        self.prof	= Val('prof')
        self.wait	= { 'auth' : 0, 'stations' : 0, 'flush' : 0, 'scan' : 0, 'next' : 0 }
        self.silent	= xbmc.translatePath("special://home/addons/%s/resources/media/silent.m4a" % _id)

        musicbrainzngs.set_useragent("kodi.%s" % _id, Val('version'))
        xbmcvfs.mkdirs(xbmc.translatePath(Val('cache')).decode("utf-8"))
        xbmcvfs.mkdirs(xbmc.translatePath(Val('library')).decode("utf-8"))


    def Proxy(self):
        Log('def Proxy ', None, xbmc.LOGDEBUG)
        proxy = Val('proxy')

        if proxy == '1':	# None
            hand = urllib2.ProxyHandler({})
            return urllib2.build_opener(hand)

        elif proxy == '0':	# Global
            if (Val('sni') == 'true') and _urllib3:
                return urllib3.PoolManager()
            else:
                return urllib2.build_opener()

        elif proxy == '2':	# Custom
            http = "http://%s:%s@%s:%s" % (Val('proxy_user'), Val('proxy_pass'), Val('proxy_host'), Val('proxy_port'))

            if (Val('sni') == 'true') and _urllib3:
                return urllib3.ProxyManager(http)
            else:
                hand = urllib2.ProxyHandler({ 'http' : http, 'https' : http })
                return urllib2.build_opener(hand)


    def Auth(self):
        Log('def Auth ', None, xbmc.LOGDEBUG)
        p = Val('prof')
        if self.prof != p:
            self.wait['auth'] = 0
            self.stations = None
            self.prof = p

        if time.time() < self.wait['auth']: return True

        self.pithos.set_url_opener(self.Proxy(), (Val('sni') == 'true'))

        try: self.pithos.connect(Val('one' + p), Val('username' + p), Val('password' + p))
        except mypithos.PithosError:
            Log('Auth BAD')
            return False

        self.wait['auth'] = time.time() + (60 * 60)	# Auth every hour
        Log('Auth  OK')
        return True


    def Login(self):
        Log('def Login ', None, xbmc.LOGDEBUG)
        if (Val('sni') == 'true') and (not _urllib3):
            if xbmcgui.Dialog().yesno(Val('name'), 'SNI Support not found', 'Please install: pyOpenSSL/ndg-httpsclient/pyasn1', 'Check Settings?'):
                xbmcaddon.Addon().openSettings()
            else:
                exit()

        while not self.Auth():
            if xbmcgui.Dialog().yesno(Val('name'), '          Login Failed', 'Bad User / Pass / Proxy', '       Check Settings?'):
                xbmcaddon.Addon().openSettings()
            else:
                exit()


    def Stations(self):
        Log('def Stations ', None, xbmc.LOGDEBUG)
        if (self.stations) and (time.time() < self.wait['stations']):
            return self.stations

        if not self.Auth(): return None
        self.stations = self.pithos.get_stations()

        self.wait['stations'] = time.time() + (60 * 5)				# Valid for 5 mins
        return self.stations


    def Sorted(self):
        Log('def Sorted ', None, xbmc.LOGDEBUG)
        sort = Val('sort')
        
        stations = list(self.Stations())
        quickmix = stations.pop(0)						# Quickmix

        if   sort == '0': stations = stations					# Normal
        elif sort == '2': stations = stations[::-1]				# Reverse
        elif sort == '1': stations = sorted(stations, key=lambda s: s['title'])	# A-Z

        stations.insert(0, quickmix)						# Quickmix back on top
        return stations


    def Dir(self, handle):
        Log('def Dir ', None, xbmc.LOGDEBUG)
        self.Login()

        ic = Val('icon')
        li = xbmcgui.ListItem('New Station ...')
        li.setIconImage(ic)
        li.setThumbnailImage(ic)
        xbmcplugin.addDirectoryItem(int(handle), "%s?search=hcraes" % _base, li, True)

        for s in self.Sorted():
            li = xbmcgui.ListItem(s['title'], s['token'])
            if self.station == s: li.select(True)

            art = Val("art-%s" % s['token'])
            if not art: art = s.get('art', ic)

            li.setIconImage(art)
            li.setThumbnailImage(art)

            title = asciidamnit.asciiDammit(s['title'])
            rurl = "RunPlugin(plugin://%s/?%s)" % (_id, urllib.urlencode({ 'rename' : s['token'], 'title' : title }))
            durl = "RunPlugin(plugin://%s/?%s)" % (_id, urllib.urlencode({ 'delete' : s['token'], 'title' : title }))
            surl = "RunPlugin(plugin://%s/?%s)" % (_id, urllib.urlencode({  'thumb' : s['token'], 'title' : title }))

            li.addContextMenuItems([('Rename Station', rurl),
                                    ('Delete Station', durl),
                                    ('Select Thumb',   surl), ])

            burl = "%s?%s" % (_base, urllib.urlencode({ 'play' : s['token'] }))
            xbmcplugin.addDirectoryItem(int(handle), burl, li)
#            Log(burl)

        xbmcplugin.endOfDirectory(int(handle), cacheToDisc = False)
        Log("Dir   OK %4s" % handle)


    def Search(self, handle, query):
        Log('def Search %s ' % query, None, xbmc.LOGDEBUG)
        self.Login()

        for s in self.pithos.search(query, True):
            title = s['artist']
            title += (' - %s' % s['title']) if s.get('title') else ''

            li = xbmcgui.ListItem(title, s['token'])
            xbmcplugin.addDirectoryItem(int(handle), "%s?create=%s" % (_base, s['token']), li)

        xbmcplugin.endOfDirectory(int(handle), cacheToDisc = False)
        Log("Search   %4s '%s'" % (handle, query))


    def Info(self, s):
        Log('def Info ', None, xbmc.LOGDEBUG)
        info = { 'artist' : s['artist'], 'album' : s['album'], 'title' : s['title'], 'rating' : s['rating'] }

        if s.get('duration'):
            info['duration'] = s['duration']

        return info


    def Add(self, song):
        Log('def Add ', song, xbmc.LOGDEBUG)
        if song['token'] != 'mesg':
            self.songs[song['token']] = song

        # This line adds the line in the playlist on Kodi GUI
        li = xbmcgui.ListItem(song['artist'], song['title'], song['art'], song['art'])
        li.setProperty("%s.token" % _id, song['token'])
        li.setInfo('music', self.Info(song))

        if song.get('encoding') == 'm4a': li.setProperty('mimetype', 'audio/aac')
        if song.get('encoding') == 'mp3': li.setProperty('mimetype', 'audio/mpeg')

        Log('def Add  adding %s' % song['path'], song, xbmc.LOGDEBUG)
        self.playlist.add(song['path'], li)
        self.Scan(False)

        Log('Add   OK', song)

    
    def Queue(self, song):
        Log('def Queue ', song, xbmc.LOGDEBUG)
        self.queue.append(song)


    def Msg(self, msg):
        Log('def Msg %s ' % msg, None, xbmc.LOGDEBUG)
        if self.mesg == msg: return
        else: self.mesg = msg
        
        # added ready (true if file is ready to play and starttime to know how 
        # long it has been taking to download file
        song = { 'starttime' : time.time(), 'ready' : False, 'token' : 'mesg', 'title' : msg, 'path' : self.silent, 'artist' : Val('name'),  'album' : Val('description'), 'art' : Val('icon'), 'rating' : '' }
        self.Queue(song)

#        while True:		# Remove old messages
#            item = None
#            for pos in range(0, self.playlist.getposition() - 1):
#                try: item = self.playlist[pos]
#                except RuntimeError:
#                    item = None
#                    break
#
#                id = item.getProperty("%s.id" % _id)
#                if (id == 'mesg'):
#                    xbmc.executeJSONRPC('{"jsonrpc":"2.0", "id":1, "method":"Playlist.Remove", "params":{"playlistid":' + str(xbmc.PLAYLIST_MUSIC) + ', "position":' + str(pos) + '}}')
#                    break
#
#            if not item:
#                break


    def M3U(self, song, delete = False):
        Log('def M3U ', song, xbmc.LOGDEBUG)
        if (Val('m3u') != 'true'): return
        if (not song.get('saved', False)): return

        m3u = xbmcvfs.File(song['path_m3u'], 'r')
        lines = m3u.read().splitlines()
        m3u.close()

        if (song['path_rel'] in lines):
            if (not delete): return
            
            lines.remove(song['path_rel'])
            
        else:
            if (not xbmcvfs.exists(song['path_lib'])): return

            lines.append(song['path_rel'])

        lines = '\n'.join(lines)

        m3u = xbmcvfs.File(song['path_m3u'], 'w')
        m3u.write(lines)
        m3u.close()


    def Tag(self, song):
        Log('def Tag ', song, xbmc.LOGDEBUG)
        try:
            res = musicbrainzngs.search_recordings(limit = 1, query = song['title'], artist = song['artist'], release = song['album'], qdur = str(song['duration'] * 1000))['recording-list'][0]
            song['number'] = int(res['release-list'][0]['medium-list'][1]['track-list'][0]['number'])
            song['count']  =     res['release-list'][0]['medium-list'][1]['track-count']
            song['score']  =     res['ext:score']
            song['brain']  =     res['id']

        except:
            song['score']  = '0'

        Log("Tag%4s%%" % song['score'], song)
        return song['score'] == '100'


    def Save(self, song):
        Log('def Save ', song, xbmc.LOGDEBUG)
        if (song['title'] == 'Advertisement') or (song.get('saved')) or (not song.get('cached', False)): return
        if (Val('mode') in ('0', '3')) or ((Val('mode') == '2') and (song.get('voted') != 'up')): return
        if (not self.Tag(song)): return

        tmp = "%s.%s" % (song['path'], song['encoding'])
        if not xbmcvfs.copy(song['path_cch'], tmp):
            Log('Save BAD', song)
            return

        if   song['encoding'] == 'm4a': tag = EasyMP4(tmp)
        elif song['encoding'] == 'mp3': tag = MP3(tmp, ID3 = EasyID3)

        if tag == None:
            Log('Save BAD', song)
            xbmcvfs.delete(tmp)
            return

        tag['tracknumber']         = "%d/%d" % (song['number'], song['count'])
        tag['musicbrainz_trackid'] = song['brain']
        tag['artist']              = song['artist']
        tag['album']               = song['album']
        tag['title']               = song['title']

        if song['encoding'] == 'mp3':
            tag.save(v2_version = 3)
        else:
            tag.save()

        xbmcvfs.mkdirs(song['path_dir'])
        xbmcvfs.copy(tmp, song['path_lib'])
        xbmcvfs.delete(tmp)

        song['saved'] = True
        self.M3U(song)

        if (song.get('art', False)) and ((not xbmcvfs.exists(song['path_alb'])) or (not xbmcvfs.exists(song['path_art']))):
            try:
                strm = self.Proxy().open(song['art'])
                data = strm.read()
            except ValueError:
                Log("Save ART      '%s'" % song['art'])
#                xbmc.log(str(song))
                return

            for jpg in [ song['path_alb'], song['path_art'] ]:
                if not xbmcvfs.exists(jpg):
                    file = xbmcvfs.File(jpg, 'wb')
                    file.write(data)
                    file.close()

        Log('Save  OK', song, xbmc.LOGNOTICE)


    def Hook(self, song, size, totl):
        Log('def Hook ', song, xbmc.LOGDEBUG)
        if totl in (341980, 340554, 173310):	# empty song cause requesting to fast
            self.Msg('Too Many Songs Requested')
            Log('Cache MT', song)
            return False

        if (song['title'] != 'Advertisement') and (totl <= int(Val('adsize')) * 1024):
            Log('Cache AD', song)

            song['artist'] = Val('name')
            song['album']  = Val('description')
            song['art']    = Val('icon')
            song['title']  = 'Advertisement'

            if (Val('skip') == 'true'):
                song['qued'] = True
                self.Msg('Skipping Advertisements')

        Log('Cache QU: ready=%s size=%8d bitrate:%8d' % (song.get('ready'), size, song['bitrate']), song, xbmc.LOGDEBUG)
        if song.get('ready',False) and (not song.get('qued')) and (size >= (song['bitrate'] / 8 * 1024 * int(Val('delay')))):
            song['qued'] = True
            self.Queue(song)

        return True


    def Cache(self, song):
        Log('def Cache ', song, xbmc.LOGDEBUG)
        try:
            strm = self.Proxy().open(song['url'], timeout = 10)
        except: # HTTPError:
            self.wait['auth'] = 0
            if not self.Auth():
                Log("Cache ER", song)
                return
            strm = self.Proxy().open(song['url'], timeout = 10)

        totl = int(strm.headers['Content-Length'])
        size = 0

        Log("Expecting %8d bytes " % totl, song)

        cont = self.Hook(song, size, totl)
        if not cont: return

        file = xbmcvfs.File(song['path_cch'], 'wb')
        self.downloading = self.downloading + 1
        song['starttime'] = time.time()
        lastnotify = time.time()
        notification('Caching', '[COLOR lime]' + song['title'] + ' [/COLOR]' , '3000', iconart)
        while (cont) and (size < totl) and (not xbmc.abortRequested) and (not self.abort):
            Log("Downloading %8d bytes, currently %8d bytes " % (totl, size), song, xbmc.LOGDEBUG)
            try: data = strm.read(min(8192, totl - size))
            except socket.timeout:
                Log('Socket Timeout: Bytes Received %8d: Cache TO' % size, song)
                song['ready'] = True
                break

            file.write(data)
            size += len(data)

            if ( lastnotify + 60 < time.time() ):
                lastnotify = time.time()
                notification('Slow Network', '[COLOR lime] %d' % (size * 100 / totl ) + '% ' + song['title'] + ' [/COLOR]' , '5000', iconart)
		

            if ( size >= totl ): 
                Log('Setting song to ready ', song, xbmc.LOGDEBUG)
                song['ready'] = True
            cont = self.Hook(song, size, totl)

        file.close()
        strm.close()
        self.downloading = self.downloading - 1

        if (not cont) or (size != totl):
            #xbmc.sleep(3000)
            xbmcvfs.delete(song['path_cch'])
            Log('Cache RM', song)

        else:
            song['cached'] = True
            self.Save(song)

        Log('Cache Download Complete, Still Downloading:%d' % self.downloading, song)


    def Fetch(self, song):
        Log('def Fetch ', song, xbmc.LOGDEBUG)
        if xbmcvfs.exists(song['path_mp3']):	# Found MP3 in Library
            Log('Song MP3', song)
            song['path_lib'] = song['path_mp3']
            song['path'] = song['path_lib']
            song['saved'] = True

        elif xbmcvfs.exists(song['path_m4a']):	# Found M4A in Library
            Log('Song M4A', song)
            song['path_lib'] = song['path_m4a']
            song['path'] = song['path_lib']
            song['saved'] = True

        elif xbmcvfs.exists(song['path_cch']):	# Found in Cache
            Log('Song CCH', song)
            song['path'] = song['path_cch']

        elif Val('mode') == '0':		# Stream Only
            Log('Song PAN', song)
            song['path'] = song['url']

        else:					# Cache / Save
            Log('Song GET', song)
            song['path'] = song['path_cch']
            self.Cache(song)
            return

        self.Queue(song)



    def Seed(self, song):
        Log('def Seed ', None, xbmc.LOGDEBUG)
        if not self.Stations(): return
        result = self.pithos.search("%s by %s" % (song['title'], song['artist']))[0]

        if (result['title'] == song['title']) and (result['artist'] == song['artist']):
            self.pithos.seed_station(song['station'], result['token'])
        else:
            Log('Seed BAD', song)


    def Branch(self, song):
        Log('def Branch ', None, xbmc.LOGDEBUG)
        if not self.Stations(): return
        station = self.pithos.branch_station(song['token'])

        Prop('play', station['token'])
        Prop('action', 'play')

        Log('Branch  ', song)


#    def Del(self, song):
#        self.M3U(song, True)
#        xbmcvfs.delete(song['path_lib'])


    def Rate(self, mode):
        Log('def Rate ', None, xbmc.LOGDEBUG)
        pos  = self.playlist.getposition()
        item = self.playlist[pos]
        tokn = item.getProperty("%s.token" % _id)
        song = self.songs.get(tokn)

        if not song:
            return

        elif (mode == 'branch'):
            self.Branch(song)
            return

        elif (mode == 'seed'):
            self.Seed(song)

        elif (mode == 'up'):
            song['voted'] = 'up'
	    Prop('voted', 'up')
            self.pithos.add_feedback(song['token'], True)
            self.Save(song)

        elif (mode == 'tired'):
            self.player.playnext()
            self.pithos.set_tired(song['token'])

        elif (mode == 'down'):
            song['voted'] = 'down'
	    Prop('voted', 'down')
            self.player.playnext()
            self.pithos.add_feedback(song['token'], False)
            self.M3U(song, True)

        elif (mode == 'clear'):
            song['voted'] = ''
	    Prop('voted', '')
            feedback = self.pithos.add_feedback(song['token'], True)
            self.pithos.del_feedback(song['station'], feedback)

        else: return

        Log("%-8s" % mode.title(), song, xbmc.LOGNOTICE)


    def Rated(self, song, rating):
        Log("Rate %1s>%1s" % (song['rating'], rating), song, xbmc.LOGNOTICE)

        expert = (Val('rating') == '1')
        song['rating'] = rating
        song['rated'] = rating

        if (rating == '5'):
            if (expert):
                self.Branch(song)
            else:
                self.pithos.add_feedback(song['token'], True)
            self.Save(song)

        elif (rating == '4'):
            if (expert):
                self.Seed(song)
            else:
                self.pithos.add_feedback(song['token'], True)
            self.Save(song)

        elif (rating == '3'):
            self.pithos.add_feedback(song['token'], True)
            self.Save(song)

        elif (rating == '2'):
            if (expert):
                self.pithos.set_tired(song['token'])
            else:
                self.pithos.add_feedback(song['token'], False)
            self.player.playnext()

        elif (rating == '1'):
            self.pithos.add_feedback(song['token'], False)
            self.player.playnext()

        elif (rating == ''):
            feedback = self.pithos.add_feedback(song['token'], True)
            self.pithos.del_feedback(song['station'], feedback)


    def Scan(self, rate = False):
        Log('def Scan ', None, xbmc.LOGDEBUG)
        if ((rate) and (time.time() < self.wait['scan'])) or (xbmcgui.getCurrentWindowDialogId() == 10135): return
        self.wait['scan'] = time.time() + 15

        songs = dict()
        for pos in range(0, self.playlist.size()):
            tk = self.playlist[pos].getProperty("%s.token" % _id)
            rt = xbmc.getInfoLabel("MusicPlayer.Position(%d).Rating" % pos)
            if (rt == ''): rt = '0'

            if tk in self.songs:
                song = self.songs[tk]
                del self.songs[tk]
                songs[tk] = song

                if (rate) and (song.get('rating', rt) != rt):
                    self.Rated(song, rt)
                elif not song.get('rating'):
                    song['rating'] = rt

        for s in self.songs:
            if (not self.songs[s].get('keep', False)) and xbmcvfs.exists(self.songs[s].get('path_cch')):
                xbmcvfs.delete(self.songs[s]['path_cch'])
                Log('Scan  RM', self.songs[s])

        self.songs = songs


    def Path(self, s):
        Log('def Path ', None, xbmc.LOGDEBUG)
        lib  = Val('library')
        badc = '\\/?%*:|"<>.'		# remove bad filename chars

        s['artist'] = ''.join(c for c in s['artist'] if c not in badc)
        s['album']  = ''.join(c for c in s['album']  if c not in badc)
        s['title']  = ''.join(c for c in s['title']  if c not in badc)

        s['path_cch'] = xbmc.translatePath(asciidamnit.asciiDammit("%s/%s - %s.%s"            % (Val('cache'), s['artist'], s['title'],  s['encoding'])))
        s['path_dir'] = xbmc.translatePath(asciidamnit.asciiDammit("%s/%s/%s - %s"            % (lib,          s['artist'], s['artist'], s['album'])))
        s['path_m4a'] = xbmc.translatePath(asciidamnit.asciiDammit("%s/%s/%s - %s/%s - %s.%s" % (lib,          s['artist'], s['artist'], s['album'], s['artist'], s['title'], 'm4a'))) #s['encoding'])))
        s['path_mp3'] = xbmc.translatePath(asciidamnit.asciiDammit("%s/%s/%s - %s/%s - %s.%s" % (lib,          s['artist'], s['artist'], s['album'], s['artist'], s['title'], 'mp3'))) #s['encoding'])))
        s['path_lib'] = xbmc.translatePath(asciidamnit.asciiDammit("%s/%s/%s - %s/%s - %s.%s" % (lib,          s['artist'], s['artist'], s['album'], s['artist'], s['title'], s['encoding'])))
        s['path_alb'] = xbmc.translatePath(asciidamnit.asciiDammit("%s/%s/%s - %s/folder.jpg" % (lib,          s['artist'], s['artist'], s['album'])))
        s['path_art'] = xbmc.translatePath(asciidamnit.asciiDammit("%s/%s/folder.jpg"         % (lib,          s['artist']))) #.decode("utf-8")

        title = ''.join(c for c in self.station['title'] if c not in badc)
        s['path_m3u'] = xbmc.translatePath(asciidamnit.asciiDammit("%s/%s.m3u"                % (lib, title)))
        s['path_rel'] = xbmc.translatePath(asciidamnit.asciiDammit(   "%s/%s - %s/%s - %s.%s" % (     s['artist'], s['artist'], s['album'], s['artist'], s['title'], s['encoding'])))


    def Fill(self):
        Log('def Fill ', None, xbmc.LOGDEBUG)
        token = self.station['token']
        if len(self.ahead.get(token, '')) > 0: return

        if not self.Auth():
            self.Msg('Login Failed. Check Settings')
            self.abort = True
            return

        try: songs = self.pithos.get_playlist(token, int(Val('quality')))
        except (mypithos.PithosTimeout, mypithos.PithosNetError): pass
        except (mypithos.PithosAuthTokenInvalid, mypithos.PithosAPIVersionError, mypithos.PithosError) as e:
            Log("%s, %s" % (e.message, e.submsg))
            self.Msg(e.message)
            self.abort = True
            return

        for song in songs:
            self.Path(song)

        self.ahead[token] = collections.deque(songs)

        Log('Fill  OK', self.station)


    def Next(self):
        Log('def Next %s %s' % (time.time(), self.wait['next']), None, xbmc.LOGDEBUG)
        # keeps the number of downloads clamped to _maxdownloads
        if time.time() < self.wait['next'] or self.downloading >= _maxdownloads: return
        self.wait['next'] = time.time() + float(Val('delay')) + 1

        self.Fill()

        token = self.station['token']
        if len(self.ahead.get(token, '')) > 0:
            song = self.ahead[token].popleft()
            threading.Timer(0, self.Fetch, (song,)).start()


    def List(self):
        Log('def List ', None, xbmc.LOGDEBUG)
        if (not self.station) or (not self.player.isPlayingAudio()): return

        len1  = self.playlist.size()
        pos  = self.playlist.getposition()
        item = self.playlist[pos]
        tokn = item.getProperty("%s.token" % _id)

        if tokn in self.songs:
            Prop('voted', self.songs[tokn].get('voted', ''))

#        skip = xbmc.getInfoLabel("MusicPlayer.Position(%d).Rating" % pos)
#        skip = ((tokn == 'mesg') or (skip == '1') or (skip == '2')) and (xbmcgui.getCurrentWindowDialogId() != 10135)
        
        # keep adding until number of max downloads is in list not played
     
        if ((len1 - pos) < 2) or ((len1 - pos + self.downloading) < (_maxdownloads + 1)):
            self.Next()

        if ((len1 - pos) > 1) and (tokn == 'mesg'):
            self.player.playnext()


    def Deque(self):
        Log('def Deque %2d' % len(self.queue), None, xbmc.LOGDEBUG)
        if len(self.queue) == 0: return
        elif self.once:
            self.playlist.clear()
            self.Flush()

        while len(self.queue) > 0:
            song = self.queue.popleft()
            self.Add(song)
            self.M3U(song)

        if self.once:
            # this will start the  playlist playing
            self.player.play(self.playlist)
            Log('def Deque setting once to False', None, xbmc.LOGDEBUG)
            self.once = False 

        max = int(Val('history'))
        while (self.playlist.size() > max) and (self.playlist.getposition() > 0):
            xbmc.executeJSONRPC('{"jsonrpc":"2.0", "id":1, "method":"Playlist.Remove", "params":{"playlistid":' + str(xbmc.PLAYLIST_MUSIC) + ', "position":0}}')

        if xbmcgui.getCurrentWindowId() == 10500:
            xbmc.executebuiltin("Container.Refresh")


    def Tune(self, token):
        Log('def Tune %s' % token, None, xbmc.LOGDEBUG)
        for s in self.Stations():
            if (token == s['token']) or (token == s['token'][-4:]):
                if self.station == s: return False

                self.station = s
                Val('station' + self.prof, token)
                return True

        return False


    def Play(self, token):
        Log('Play  ??', self.station, xbmc.LOGNOTICE)
        last = self.station

        if self.Tune(token):
            self.Fill()

            while True:
                len = self.playlist.size() - 1
                pos = self.playlist.getposition()
                if len > pos:
                    item = self.playlist[len]
                    tokn = item.getProperty("%s.token" % _id)

                    if (last) and (tokn in self.songs):
                        self.songs[tokn]['keep'] = True
                        self.ahead[last['token']].appendleft(self.songs[tokn])

                    xbmc.executeJSONRPC('{"jsonrpc":"2.0", "id":1, "method":"Playlist.Remove", "params":{"playlistid":' + str(xbmc.PLAYLIST_MUSIC) + ', "position":' + str(len) + '}}')
                else: break

            self.Msg("%s" % self.station['title'])
            Log('Play  OK', self.station, xbmc.LOGNOTICE)

        xbmc.executebuiltin('ActivateWindow(10500)')


    def Create(self, token):
        Log('%s' % token)
        self.Stations()
#        self.Auth()
        station = self.pithos.create_station(token)

        Log('Create  ', station)
        self.Play(station['token'])


    def Delete(self, token):
        if (self.station) and (self.station['token'] == token): self.station = None

        self.Stations()
        station = self.pithos.delete_station(token)

        Log('Delete  ', station, xbmc.LOGNOTICE)
        xbmc.executebuiltin("Container.Refresh")


    def Rename(self, token, title):
        self.Stations()
        station = self.pithos.rename_station(token, title)

        Log('Rename  ', station)
        xbmc.executebuiltin("Container.Refresh")


    def Action(self):
        act = Prop('action')
        Log('def Action action=%s' % act, None, level = xbmc.LOGDEBUG)

        if _stamp != Prop('stamp'):
            self.abort = True
            self.station = None
            return

        elif act == '':
            Prop('run', str(time.time()))
            return

        elif act == 'search':
            self.Search(Prop('handle'), Prop('search'))
 
        elif act == 'create':
            self.Create(Prop('create'))

        elif act == 'rename':
            self.Rename(Prop('rename'), Prop('title'))

        elif act == 'delete':
            self.Delete(Prop('delete'))

        elif act == 'rate':
            self.Rate(Prop('rate'))

        act = Prop('action')

        if   act == 'play':
            self.Play(Prop('play'))

        elif act == 'dir':
            self.Dir(Prop('handle'))
            if (self.once) and (Val('autoplay') == 'true') and (Val('station' + self.prof)):
                self.Play(Val('station' + self.prof))

        Prop('action', '')
        Prop('run', str(time.time()))


    def Flush(self):
        Log('def Flush', None, level = xbmc.LOGDEBUG)
        cch = xbmc.translatePath(Val('cache')).decode("utf-8")
        reg = re.compile('^.*\.(m4a|mp3)')

        (dirs, list) = xbmcvfs.listdir(cch)

        for file in list:
            if reg.match(file):
                xbmcvfs.delete("%s/%s" % (cch, file))
                Log("Flush OK      '%s'" % file)


    def Loop(self):
        Log('def Loop', None, level = xbmc.LOGDEBUG)
        while (not xbmc.abortRequested) and (not self.abort) and (self.once or self.player.isPlayingAudio()):
            time.sleep(0.01)
            xbmc.sleep(5000)

            self.Action()
            self.Deque()
            self.List()
            self.Scan()
        if (self.player.isPlayingAudio()):
            notification('Exiting', '[COLOR lime]No longer queuing new songs[/COLOR]' , '5000', iconart)
        Log('Pankodi Exiting XBMCAbort?=%s PandokiAbort?=%s ' % (xbmc.abortRequested, self.abort), None, level = xbmc.LOGNOTICE)
        Prop('run', '0')

