from __future__ import division
from __future__ import print_function

import sys
from os import path
from argparse import ArgumentParser
import numpy as np

sys.path.append(path.dirname(path.dirname(path.dirname(path.dirname(path.abspath(__file__))))) + '/utils/')
sys.path.append(path.dirname(path.dirname(path.dirname(path.dirname(path.abspath(__file__))))) + '/CART/')


from algorithm_utils import StateData
from cart_lib import Cart_Glob2Loc_TD, CartIter1_Loc2Glob_TD, cart_step_1_global

def main():
    # Parse arguments
    parser = ArgumentParser()
    parser.add_argument('-no_split_points', required=True, type=int, help='Number of split points')
    parser.add_argument('-cur_state_pkl', required=True, help='Path to the pickle file holding the current state.')
    parser.add_argument('-prev_state_pkl', required=True, help='Path to the pickle file holding the previous state.')
    parser.add_argument('-local_step_dbs', required=True, help='Path to db holding local step results.')
    args, unknown = parser.parse_known_args()
    fname_cur_state = path.abspath(args.cur_state_pkl)
    fname_prev_state = path.abspath(args.prev_state_pkl)
    local_dbs = path.abspath(args.local_step_dbs)

    # Load global state
    global_state = StateData.load(fname_prev_state).data
    # Load local nodes output
    activePaths = CartIter1_Loc2Glob_TD.load(local_dbs).get_data()

    # Run algorithm global iteration step
    activePaths = cart_step_1_global(global_state['args_X'], global_state['args_Y'], global_state['CategoricalVariables'], activePaths, args.no_split_points)

    global_out = Cart_Glob2Loc_TD(  global_state['globalTree'], activePaths )
    # Save global state
    # Save global state
    global_state = StateData(   stepsNo = global_state['stepsNo'] + 1 ,
                                args_X = global_state['args_X'],
                                args_Y = global_state['args_Y'],
                                CategoricalVariables = global_state['CategoricalVariables'],
                                globalTree = global_state['globalTree'],
                                activePaths = activePaths,
                                t1 = global_state['t1'] )

    global_state.save(fname=fname_cur_state)
    # Return the algorithm's output
    global_out.transfer()

if __name__ == '__main__':
    main()
