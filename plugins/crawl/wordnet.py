'''
wordnet.py

Copyright 2006 Andres Riancho

This file is part of w3af, w3af.sourceforge.net .

w3af is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation version 2 of the License.

w3af is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with w3af; if not, write to the Free Software
Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

'''
from itertools import chain, repeat, izip

from core.controllers.plugins.crawl_plugin import CrawlPlugin
from core.controllers.core_helpers.fingerprint_404 import is_404
from core.controllers.misc.levenshtein import relative_distance_lt

from core.data.fuzzer.fuzzer import createMutants
from core.data.nltk_wrapper.nltk_wrapper import wn
from core.data.options.option import option
from core.data.options.optionList import optionList


class wordnet(CrawlPlugin):
    '''
    Use the wordnet lexical database to find new URLs.
    
    @author: Andres Riancho (andres.riancho@gmail.com)
    '''
    
    def __init__(self):
        CrawlPlugin.__init__(self)
        
        # User defined parameters
        self._wordnet_results = 5
        
    def crawl(self, fuzzable_request ):
        '''
        @parameter fuzzable_request: A fuzzable_request instance that contains
                                    (among other things) the URL to test.
        '''
        original_response = self._uri_opener.send_mutant(fuzzable_request)        
        original_response_repeat = repeat(original_response)
        
        mutants = self._generate_mutants( fuzzable_request )
        
        args = izip(original_response_repeat, mutants)
        
        #   Send the requests using threads:
        self._tm.threadpool.map_multi_args(self._check_existance,
                                           args)
    
    def _check_existance( self, original_response, mutant ):
        '''
        Actually check if the mutated URL exists.
        
        @return: None, all important data is saved to self.out_queue
        '''
        response = self._uri_opener.send_mutant(mutant)
        if not is_404( response ) and \
        relative_distance_lt(original_response.body, response.body, 0.85):
            for fr in self._create_fuzzable_requests( response ):
                self.output_queue.put(fr)
    
    def _generate_mutants( self, fuzzable_request ):
        '''
        Based on the fuzzable request, i'll search the wordnet database and generated
        A LOT of mutants.
        
        @return: A list of mutants.
        '''
        return chain( self._generate_fname( fuzzable_request ) ,
                      self._generate_qs( fuzzable_request ) )
    
    def _generate_qs( self, fuzzable_request ):
        '''
        Check the URL query string.
        @return: A list of mutants.
        '''     
        query_string = fuzzable_request.getURI().querystring
        for parameter_name in query_string:
            # this for loop was added to address the repeated parameter name issue
            for element_index in xrange(len(query_string[parameter_name])):
                wordnet_result = self._search_wn( query_string[parameter_name][element_index] )
                new_urls = self._generate_URL_from_wn_result( parameter_name, element_index, 
                                                              wordnet_result, fuzzable_request )
                for u in new_urls:
                    yield u
                
                
    def _search_wn( self, word ):
        '''
        Search the wordnet for this word, based on user options.
        
        @return: A list of related words.
        
        >>> wn = wordnet()
        >>> wn_result = wn._search_wn('blue')
        >>> len(wn_result) == wn._wordnet_results
        True
        >>> 'red' in wn_result
        True
        
        '''
        if not word:
            return []
        
        if word.isdigit():
            return []
        
        result = []
        
        # Now the magic that gets me a lot of results:
        try:
            result.extend( wn.synsets(word)[0].hypernyms()[0].hyponyms() )
        except:
            pass
        
        synset_list = wn.synsets( word )
        
        for synset in synset_list:
            
            # first I add the synsec as it is:
            result.append( synset )
            
            # Now some variations...
            result.extend( synset.hypernyms() )
            result.extend( synset.hyponyms() )
            result.extend( synset.member_holonyms() )
            result.extend( synset.lemmas[0].antonyms() )

        # Now I have a results list filled up with a lot of words, the problem is that
        # this words are really Synset objects, so I'll transform them to strings:
        result = [ i.name.split('.')[0] for i in result]
        
        # Another problem with Synsets is that the name is "underscore separated"
        # so, for example:
        # "big dog" is "big_dog"
        result = [ i.replace('_', ' ') for i in result]
        
        # Now I make a "uniq"
        result = list(set(result))
        if word in result: result.remove(word)
        
        # The next step is to order each list by popularity, so I only send to the web
        # the most common words, not the strange and unused words.
        result = self._popularity_contest( result )
        
        # Respect the user settings
        result = result[:self._wordnet_results]
        
        return result
    
    def _popularity_contest( self, result ):
        '''
        @parameter results: The result map of the wordnet search.
        @return: The same result map, but each item is ordered by popularity
        '''
        def sort_function( i, j ):
            '''
            Compare the lengths of the objects.
            '''
            return cmp( len( i ) , len( j ) )
        
        result.sort( sort_function )
            
        return result
    
    def _generate_fname( self, fuzzable_request ):
        '''
        Check the URL filenames
        @return: A list mutants.
        '''
        url = fuzzable_request.getURL()
        fname = self._get_filename( url )
        
        wordnet_result = self._search_wn( fname )
        new_urls = self._generate_URL_from_wn_result( None, None, wordnet_result, fuzzable_request )
        return new_urls
    
    def _get_filename( self, url ):
        '''
        @return: The filename, without the extension
        '''
        fname = url.getFileName()
        ext = url.getExtension()
        return fname.replace('.' + ext, '')
            
    def _generate_URL_from_wn_result( self, analyzed_variable, element_index, 
                                      result_set, fuzzable_request ):
        '''
        Based on the result, create the new URLs to test.
        
        @parameter analyzed_variable: The parameter name that is being analyzed
        @parameter element_index: 0 in most cases, >0 if we have repeated parameter names
        @parameter result_set: The set of results that wordnet gave use
        @parameter fuzzable_request: The fuzzable request that we got as input in the first place.
        
        @return: An URL list.
        '''
        if analyzed_variable is None:
            # The URL was analyzed
            url = fuzzable_request.getURL()
            fname = url.getFileName()
            domain_path = url.getDomainPath()
            
            # The result
            result = []
            
            splitted_fname = fname.split('.')
            if len(splitted_fname) == 2:
                name = splitted_fname[0]
                extension = splitted_fname[1]
            else:
                name = '.'.join(splitted_fname[:-1])
                extension = 'html'
            
            for set_item in result_set:
                new_fname = fname.replace( name, set_item )
                frCopy = fuzzable_request.copy()
                frCopy.setURL( domain_path.urlJoin( new_fname ) )
                result.append( frCopy )
                
            return result
            
        else:
            mutants = createMutants( fuzzable_request , result_set,
                                     fuzzableParamList=[analyzed_variable,] )
            return mutants
        
    def get_options( self ):
        '''
        @return: A list of option objects for this plugin.
        '''
        ol = optionList()
        
        d = 'Only use the first wnResults (wordnet results) from each category.'
        o = option('wn_results', self._wordnet_results, d, 'integer')
        ol.add(o)
        
        return ol

    def set_options( self, optionList ):
        '''
        This method sets all the options that are configured using the user interface 
        generated by the framework using the result of get_options().
        
        @param optionList: A dictionary with the options for the plugin.
        @return: No value is returned.
        ''' 
        self._wordnet_results = optionList['wn_results'].getValue()

    def getLongDesc( self ):
        '''
        @return: A DETAILED description of the plugin functions and features.
        '''
        return '''
        This plugin finds new URL's using wn.
        
        An example is the best way to explain what this plugin does, let's 
        suppose that the input for this plugin is:
            - http://a/index.asp?color=blue
    
        The plugin will search the wordnet database for words that are related
        with "blue", and return for example: "black" and "white". So the plugin
        requests this two URL's:
            - http://a/index.asp?color=black
            - http://a/index.asp?color=white
        
        If the response for those URL's is not a 404 error, and has not the same
        body content, then we have found a new URI. The wordnet database is
        bundled with w3af, more information about wordnet can be found at:
        http://wn.princeton.edu/
        '''
